"""ИИ-видео: этапы генерации, деньги и одобрение (docs/design/ai-video-projects.md §3.5–3.6).

Этапы (решение владельца 2026-11-01 — одобрение ПО ЭТАПАМ):

* ``refs`` — стиль-библия (картинки настроения), плиты локаций, листы персонажей (разворот,
  эмоции, облики);
* ``blocking`` — (v3, 0 ₽) 3D-макет каждого плана в нашем движке сцен (``app/aivideo_blocking.py``):
  скриншоты старта и конца — первый референс ключевых кадров;
* ``keyframes`` — пары ключевых кадров: старт плана и (у плана с видео) конец; конец плана N — он же
  старт сцепленного N+1 (один файл, N+1 кадров на цепочку);
* ``voice`` — озвучка реплик голосами персонажей (TTS из ``app/dubbing.py``);
* ``video`` — видео плана из пары кадров (режим первый+последний кадр; режим — по возможностям модели,
  кадры и листы персонажей у Seedance/H3 вместе не шлются). Готовое видео бесплатно проверяет Gemini.

Каждый этап: ``estimate`` (бесплатно) → ``request_stage`` → карточка владельцу в очереди одобрения
(UI «Одобрение», баннер на странице проекта, Telegram) с суммой → «Разрешить» → работник запускает
одобренные запуски. Перегенерация одной картинки — свой маленький расчёт и своя карточка.
Агент может только ПРОСИТЬ (карточку), решает владелец.

Платный запуск идёт, только если одновременно:

* ``AIVIDEO_ENABLED=1`` (включает владелец);
* у запуска одобренная карточка, и потраченное по ней + оценка ≤ одобренной суммы;
* серия: потрачено + оценка ≤ потолка серии (черновик 350 ₽, качество 2 500 ₽);
* месяц проекта (``month_cap_rub`` проекта, не выше общего) и месяц всех ИИ-видео вместе
  (``AIVIDEO_MONTH_CAP_RUB``); сумма одной карточки ≤ ``AIVIDEO_MAX_BUDGET_RUB``.

Провайдер ``none`` (сухой прогон) — 0 ₽: карточка не нужна, весь конвейер идёт сразу.

Двойной оплаты нет: запуск берёт ровно один исполнитель (работник, ``aiv_run_approved``, другой
процесс) — проверка денег и ``approved → submitting`` одной транзакцией ``BEGIN IMMEDIATE``, все
дальнейшие смены статуса — «сравнить-и-записать», второй «готово» не пишет ни версию, ни трату;
``remote_id`` пишется в БД ДО опроса, после перезапуска опрос продолжается
(``resume``) без нового POST; запуск, оборванный между «отправляю» и ответом провайдера, не
повторяется сам (``failed``: «проверь баланс»). Одинаковый запрос (``input_hash``) берёт готовый
результат. Каждое списание — строка ``aiv_ledger`` (факт провайдера, разница баланса или оценка).
Задача, упавшая у провайдера (ошибка, модерация, «все провайдеры вернули ошибку»), освобождает резерв
и в траты не идёт, если провайдер сам не назвал списание.

Повтор запроса этапа (двойной клик, повтор после ошибки клиента) второй раз не заказывает: цель с
ждущим/идущим запуском пропускается, та же ждущая карточка возвращается как есть; повтор того же
решения по карточке (``decide_card``) ничего не запускает. Явная перегенерация — как раньше.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import aivideo as V
from app import autopilot as A
from app import projects as P
from app.aivideo_providers import (
    PRICE_CHECKED,
    RESOLUTION,
    VIDEO_MODE_LABELS,
    ProviderError,
    SubmitUncertain,
    choose_video_mode,
    default_video_model,
    image_price,
    video_capabilities,
    max_refs,
    model_params,
    video_params,
    video_price,
    video_seconds_range,
    network_phase,
)
from app.settings import settings

_log = logging.getLogger(__name__)

STAGES = ("refs", "blocking", "keyframes", "voice", "video")
STAGE_LABELS = {"refs": "референсы (стиль, локации, листы персонажей)",
                "blocking": "блокинг планов (3D-макет композиции, 0 ₽)",
                "keyframes": "ключевые кадры планов (старт + конец)",
                "voice": "озвучка реплик", "video": "видео планов (первый+последний кадр)"}
EPISODE_CAP_RUB = {"draft": 350.0, "quality": 2500.0}
STYLE_REF_COUNT = 3
CARD_ACTION = "aiv_budget"
# recovering — POST ушёл без ответа (504/обрыв), адаптер умеет искать запрос у провайдера
# (``recover``): id ищем, POST не повторяем. uncertain (1.1) — ответ не дошёл, искать нечем:
# сверка баланса, повтор — только если точно не оплачено.
ACTIVE = ("approved", "submitting", "polling", "recovering")
SUBMIT_STALE_SEC = 600  # «отправляю» дольше — исполнитель другого процесса умер (POST ждёт ≤ 120 с)
VIDEO_MIN_SEC = 4  # план-видео 4–8 с (у seedance меньше 4 не бывает)
VIDEO_MAX_SEC = 8
# Поиск запроса у провайдера после обрыва ответа: сразу RECOVER_ATTEMPTS раз через
# RECOVER_INTERVAL_SEC, дальше — каждым проходом работника; не нашли за срок адаптера
# (``recover_deadline_sec``, по умолчанию RECOVER_DEADLINE_SEC) — «под вопросом» (uncertain).
RECOVER_ATTEMPTS = 6
RECOVER_INTERVAL_SEC = 10.0
RECOVER_DEADLINE_SEC = 20 * 60
OPEN = ("awaiting",) + ACTIVE
# Сеть до провайдера рвётся (TLS EOF, таймауты). До отправки тела — повтор сам, с паузой;
# после — «под вопросом» (uncertain): сверка баланса, сам не повторяю.
SUBMIT_TRIES = 3
RETRY_BACKOFF_SEC = (2.0, 6.0)
BALANCE_SETTLE_SEC = 3.0  # провайдер списывает не мгновенно — баланс «после» чуть позже
STOP_HTTP = (401, 402, 403)  # ключ/деньги/доступ — дальше всё равно откажет: стоп партии
STYLE_BASE = {
    "anime": "2D anime / toon style, clean line art, cel shading, flat colors, expressive faces",
    "photoreal": "photorealistic, cinematic lighting, natural skin texture, 35mm film still",
}
NO_TEXT = "No text, no letters, no captions, no watermark, no logo."
REF_ASPECT = {"style": "9:16", "location": "9:16", "turnaround": "16:9", "expressions": "3:2", "look": "16:9",
              "keyframe": "9:16", "endframe": "9:16"}
REF_LABELS = {"style": "стиль", "location": "плита локации", "turnaround": "разворот", "expressions": "эмоции",
              "look": "облик", "keyframe": "стартовый кадр", "endframe": "конечный кадр", "line_audio": "реплика",
              "shot_video": "видео плана", "blocking_start": "блокинг: старт", "blocking_end": "блокинг: конец"}
FRAME_KINDS = ("keyframe", "endframe")
# Видео: ограничения, которые держат физику (docs/design/ai-video-models-2026-11.md §«Изменения»).
VIDEO_NEGATIVE = ("cuts, scene change, transitions, second shot, duplicate objects, extra characters, extra robots, "
                  "floating objects, objects appearing or vanishing, morphing, characters changing design, humanoid "
                  "version of a non-humanoid character, text, captions, watermark")

_LOCK = threading.RLock()
_ACTIVE_JOBS: set[int] = set()  # опрашиваются сейчас (не опрашивать вторым потоком)
_INFLIGHT: set[int] = set()  # взяты в работу этим процессом (resume их не трогает)
_wake: threading.Event | None = None


class BudgetError(V.AivError):
    """Этап дороже потолка / выключено / нет одобрения — ничего не запущено."""


# --------------------------------------------------------------------------- #
# Расширения: свой этап со своими целями на той же машинерии денег и запусков
# (объяснялки app/explainer.py: позы маскота, лист, озвучка; истории app/stories.py: сцены, листы актёров,
# плиты локаций, кадр стиля, озвучка). Расширение —
# объект с полями ``stage``, ``label``, ``target_types``, ``ref_kinds`` (вид → подпись), ``card_cap_rub``
# и методами ``plan(store, project_id, items, regenerate) → [item]``, ``build(store, job) → (prompt,
# refs, caption, info)`` (как ``build_request``), ``project_of(store, target_type, target_id) → id``;
# необязательно ``card_line(store, project_id, est) → str`` — первая строка карточки владельцу,
# ``deps(store, job) → [(target_id, "тип:вид")]`` — что в той же партии должно быть готово раньше,
# ``card_prefix`` (заголовок карточки) и ``cap_hint`` (подсказка, когда сумма выше потолка ролика).
# Карточка, одобрение, потолки месяца, атомарный захват, восстановление после 504 и журнал трат —
# общие: расширение их не трогает.
# --------------------------------------------------------------------------- #

EXTENSIONS: dict[str, Any] = {}


def register_extension(ext: Any) -> None:
    EXTENSIONS[ext.stage] = ext
    STAGE_LABELS[ext.stage] = ext.label
    REF_LABELS.update(ext.ref_kinds)


def _ext_of_target(target_type: str) -> Any:
    return next((e for e in EXTENSIONS.values() if target_type in e.target_types), None)


def _ext_of_kind(ref_kind: str) -> Any:
    return next((e for e in EXTENSIONS.values() if ref_kind in e.ref_kinds), None)


# --------------------------------------------------------------------------- #
# Деньги: траты, потолки
# --------------------------------------------------------------------------- #


def _sum(store: Any, sql: str, params: tuple) -> float:
    row = store.db.query_one(sql, params)
    return round(float((row[0] if row else 0) or 0), 2)


def spent_month(store: Any, project_id: int | None = None) -> float:
    sql = "SELECT COALESCE(SUM(amount_rub), 0) FROM aiv_ledger WHERE created_at >= date('now', 'start of month')"
    if project_id is None:
        return _sum(store, sql, ())
    return _sum(store, sql + " AND project_id = ?", (int(project_id),))


def spent_script(store: Any, script_id: int) -> float:
    return _sum(store, "SELECT COALESCE(SUM(amount_rub), 0) FROM aiv_ledger WHERE script_id = ?", (int(script_id),))


def spent_approval(store: Any, approval_id: int) -> float:
    return _sum(store, "SELECT COALESCE(SUM(amount_rub), 0) FROM aiv_ledger WHERE approval_id = ?",
                (int(approval_id),))


def _reserved(store: Any, where: str, params: tuple, statuses: tuple = OPEN, exclude_approval: Any = None) -> float:
    """Зарезервировано: оценки ждущих и идущих запусков (упавшие/отменённые/готовые — нет: у готовых
    трата уже в журнале, у упавших денег не взяли). ``exclude_approval`` — id карточки (или их список),
    чьи запуски не считать (карточка, которую новая заменит)."""
    marks = ", ".join("?" for _ in statuses)
    sql = (f"SELECT COALESCE(SUM(estimate_rub), 0) FROM aiv_gen_jobs WHERE status IN ({marks}) AND provider != 'none' "
           f"AND {where}")
    args = (*statuses, *params)
    excluded = ([] if exclude_approval is None else [exclude_approval] if isinstance(exclude_approval, int)
                else list(exclude_approval))
    if excluded:
        sql += f" AND COALESCE(approval_id, 0) NOT IN ({', '.join('?' for _ in excluded)})"
        args = (*args, *(int(x) for x in excluded))
    return _sum(store, sql, args)


def _month_filter() -> str:
    return "created_at >= date('now', 'start of month')"


def episode_cap(store: Any, script_id: int) -> float:
    return EPISODE_CAP_RUB.get(V.get_script(store, script_id, with_shots=False)["quality"], EPISODE_CAP_RUB["draft"])


def project_month_cap(store: Any, project_id: int) -> float:
    own = V._style_row(store, project_id).get("month_cap_rub")
    global_cap = float(settings.aivideo_month_cap_rub)
    return round(min(float(own), global_cap) if own is not None else global_cap, 2)


def budget_view(store: Any, project_id: int, script_id: int | None = None) -> dict:
    """Полоса «потрачено / зарезервировано / потолок» для UI и агента."""
    style = V._style_row(store, project_id)
    out = {
        "enabled": bool(settings.aivideo_enabled), "provider": style["provider"], "paid": style["provider"] != "none",
        "price_checked": PRICE_CHECKED,
        "run_cap_rub": float(settings.aivideo_max_budget_rub),
        "month": {"spent_rub": spent_month(store, project_id), "cap_rub": project_month_cap(store, project_id),
                  "reserved_rub": _reserved(store, "project_id = ?", (int(project_id),))},
        "all_projects_month": {"spent_rub": spent_month(store), "cap_rub": float(settings.aivideo_month_cap_rub),
                               "reserved_rub": _reserved(store, "1 = 1", ())},
        "total_spent_rub": _sum(store, "SELECT COALESCE(SUM(amount_rub), 0) FROM aiv_ledger WHERE project_id = ?",
                                (int(project_id),)),
        "episode_caps_rub": dict(EPISODE_CAP_RUB),
    }
    if script_id is not None:
        out["episode"] = {"spent_rub": spent_script(store, script_id), "cap_rub": episode_cap(store, script_id),
                          "reserved_rub": _reserved(store, "script_id = ?", (int(script_id),)),
                          "quality": V.get_script(store, script_id, with_shots=False)["quality"]}
    return out


def cap_problems(store: Any, project_id: int, script_id: int | None, amount: float, stage: str,
                 exclude_approval: Any = None, reserved: bool = True) -> list[str]:
    """Почему сумму ``amount`` нельзя потратить (пусто — можно). ``reserved`` — учитывать уже
    одобренное/ждущее, но не потраченное (при запросе карточки — да; при запуске по своей карточке — нет)."""
    out = []
    amount = round(float(amount), 2)
    run_cap = float(settings.aivideo_max_budget_rub)
    if amount > run_cap + 1e-6:
        out.append(f"{amount:.2f} ₽ больше потолка одного этапа {run_cap:.0f} ₽ (AIVIDEO_MAX_BUDGET_RUB) — "
                   "раздели на части (items)")
    ex = exclude_approval

    def res(where: str, params: tuple) -> float:
        return _reserved(store, where, params, exclude_approval=ex) if reserved else 0.0

    cap = project_month_cap(store, project_id)
    used = spent_month(store, project_id) + res("project_id = ?", (int(project_id),))
    if used + amount > cap + 1e-6:
        out.append(f"месяц проекта: потрачено и одобрено {used:.2f} ₽ + {amount:.2f} ₽ > потолка {cap:.0f} ₽")
    gcap = float(settings.aivideo_month_cap_rub)
    gused = spent_month(store) + res("1 = 1", ())
    if gused + amount > gcap + 1e-6:
        out.append(f"месяц всех ИИ-видео: {gused:.2f} ₽ + {amount:.2f} ₽ > потолка {gcap:.0f} ₽ (AIVIDEO_MONTH_CAP_RUB)")
    if script_id is not None:
        ecap = episode_cap(store, script_id)
        eused = spent_script(store, script_id) + res("script_id = ?", (int(script_id),))
        if eused + amount > ecap + 1e-6:
            out.append(f"серия: {eused:.2f} ₽ + {amount:.2f} ₽ > потолка серии {ecap:.0f} ₽")
    elif stage in EXTENSIONS:
        ecap = float(getattr(EXTENSIONS[stage], "card_cap_rub", 0) or 0)
        if ecap and amount > ecap + 1e-6:
            hint = getattr(EXTENSIONS[stage], "cap_hint", "меньше новых поз (переиспользуй библиотеку) или раздели "
                                                          "(items)")
            out.append(f"{STAGE_LABELS[stage]}: {amount:.2f} ₽ > потолка одного ролика {ecap:.0f} ₽ — {hint}")
    elif stage == "refs":
        quality = V._style_row(store, project_id)["quality"]
        ecap = EPISODE_CAP_RUB.get(quality, EPISODE_CAP_RUB["draft"])
        if amount > ecap + 1e-6:
            out.append(f"референсы за раз {amount:.2f} ₽ > потолка этапа {ecap:.0f} ₽ ({quality}) — раздели (items)")
    return out


def add_ledger(store: Any, job: dict, amount: float, source: str, note: str = "") -> None:
    store.db.execute(
        "INSERT INTO aiv_ledger (project_id, script_id, job_id, approval_id, stage, model, amount_rub, source, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job["project_id"], job.get("script_id"), job["id"], job.get("approval_id"), job["stage"], job["model"],
         round(float(amount), 4), source, note[:300]))


def ledger(store: Any, project_id: int, limit: int = 200) -> list[dict]:
    rows = store.db.query_all(
        "SELECT l.*, j.label, j.provider FROM aiv_ledger l LEFT JOIN aiv_gen_jobs j ON j.id = l.job_id "
        "WHERE l.project_id = ? ORDER BY l.id DESC LIMIT ?", (int(project_id), max(1, min(int(limit), 1000))))
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# План этапа: что генерировать и сколько стоит
# --------------------------------------------------------------------------- #


def _quality(store: Any, project_id: int, script_id: int | None) -> str:
    if script_id is not None:
        return V.get_script(store, script_id, with_shots=False)["quality"]
    return V._style_row(store, project_id)["quality"]


def _fresh(ref: dict | None) -> bool:
    return bool(ref and not ref["stale"] and ref.get("asset_id"))


def _parse_item(item: Any) -> tuple[str, int, str, str]:
    """``"character:3"`` / ``{"target_type", "target_id", "kind", "slot"}`` → (тип, id, вид, слот)."""
    if isinstance(item, str):
        parts = item.split(":")
        kind = parts[2] if len(parts) > 2 else ""
        return parts[0], int(parts[1]) if len(parts) > 1 and parts[1] else 0, kind, parts[3] if len(parts) > 3 else ""
    if isinstance(item, dict):
        return (str(item.get("target_type") or item.get("type") or ""), int(item.get("target_id") or item.get("id") or 0),
                str(item.get("kind") or ""), str(item.get("slot") or ""))
    raise V.AivError(f"элемент этапа {item!r}: строка «тип:id[:вид]» или объект {{target_type, target_id, kind}}")


def _image_item(stage: str, target_type: str, target_id: int, ref_kind: str, label: str, model: str, quality: str,
                provider: str, slot: str = "") -> dict:
    resolution = RESOLUTION.get(quality, "1K")
    price = image_price(model, resolution, provider)
    return {"stage": stage, "kind": "image", "target_type": target_type, "target_id": int(target_id),
            "ref_kind": ref_kind, "slot": slot, "label": label, "provider": provider, "model": model,
            "resolution": resolution, "aspect": REF_ASPECT.get(ref_kind, "9:16"), "estimate_rub": price["rub"],
            "price_note": price["note"]}


# Персонаж не говорит (speech sfx): реплика — звук из пака freesound (ТОЛЬКО настоящие записи,
# никакой синтетики) по эмоции реплики. Свой звук персонажа — voice.sfx («sfx:freesound/<файл>»).
SFX_DEFAULT = "sfx:freesound/pop-soft.ogg"
SFX_BY_EMOTION: list[tuple[tuple[str, ...], str]] = [
    (("sad", "груст", "печал", "tired", "устал", "disappoint", "разочар", "fail", "провал"),
     "sfx:freesound/sad-trombone.ogg"),
    (("angry", "зл", "раздраж", "annoy", "furious", "ярост", "возмущ"), "sfx:freesound/impact-low.ogg"),
    (("scared", "fear", "испуг", "страх", "нерв", "тревог", "panic", "паник"), "sfx:freesound/sting.ogg"),
    (("surpris", "удив", "shock", "шок", "изумл"), "sfx:freesound/whip.ogg"),
    (("happy", "рад", "joy", "весел", "excit", "восторг", "laugh", "смех", "смеш"), "sfx:freesound/pop.ogg"),
]


def sfx_for(character: dict | None, emotion: str = "") -> str:
    """Звук реплики персонажа-«пищалки»: его собственный (voice.sfx) или по эмоции, иначе мягкий поп."""
    own = ((character or {}).get("voice") or {}).get("sfx")
    if own:
        return str(own)
    text = str(emotion or "").casefold().replace("ё", "е")
    for words, ref in SFX_BY_EMOTION:
        if any(w in text for w in words):
            return ref
    return SFX_DEFAULT


def speech_of(store: Any, project_id: int, who: str) -> tuple[dict | None, str]:
    character = V.character_by_slug(store, project_id, who) if who and who != "narrator" else None
    return character, (character or {}).get("speech") or "voice"


def _voice_of(store: Any, project_id: int, who: str, emotion: str = "") -> dict:
    style = V._style_row(store, project_id)
    character, speech = speech_of(store, project_id, who)
    if speech == "sfx":
        return {"provider": "none", "model": "sfx", "voice": sfx_for(character, emotion)}
    voice = dict((character or {}).get("voice") or {})
    provider = "none" if style["provider"] == "none" else (voice.get("provider") or "polza")
    if provider == "gemini":  # бесплатный Gemini TTS: свои модель и голос по умолчанию
        out = {"provider": provider, "model": voice.get("model") or settings.gemini_tts_model,
               "voice": voice.get("voice") or settings.gemini_tts_voice}
        return {**out, "style": voice["style"]} if voice.get("style") else out
    return {"provider": provider, "model": voice.get("model") or settings.polza_tts_model,
            "voice": voice.get("voice") or settings.polza_tts_voice}


def _line_key(voice: dict, text: str) -> str:
    key = f"{voice['provider']}|{voice['model']}|{voice['voice']}|{text}"
    return f"{key}|{voice['style']}" if voice.get("style") else key  # без style — как раньше


def _paid(provider: str, kind: str = "") -> bool:
    """Платит ли запуск деньгами: реплики — по провайдеру TTS (gemini и none бесплатны),
    картинки и видео — всё, кроме сухого прогона."""
    if kind == "tts":
        from app.aivideo_providers import tts_paid

        return tts_paid(provider)
    return provider != "none"


def speech_need_sec(shot: dict) -> float:
    """Сколько секунд нужно плану под его реплики (~14 символов/с + вход/хвост)."""
    chars = sum(len(d.get("text") or "") for d in shot.get("dialogue") or [])
    if not chars:
        return 0.0
    return round(chars / V.CHARS_PER_SEC + 0.35 + 0.45 + 0.2 * (len(shot["dialogue"]) - 1), 2)


def video_seconds(shot: dict, model: str) -> int:
    """Длина видео плана, целые секунды 4–8: длительность плана, но не короче его реплик."""
    import math

    lo, hi = video_seconds_range(model)
    want = max(float(shot.get("duration") or 4), speech_need_sec(shot))
    return int(min(VIDEO_MAX_SEC, hi, max(VIDEO_MIN_SEC, lo, math.ceil(want - 0.05))))


def _video_item(shot: dict, script_id: int, provider: str, model: str, resolution: str, start: str) -> dict:
    seconds = video_seconds(shot, model)
    price = video_price(model, resolution, seconds, provider)
    return {"stage": "video", "kind": "video", "target_type": "shot", "target_id": int(shot["id"]),
            "ref_kind": "shot_video", "slot": "", "script_id": int(script_id),
            "label": f"план {shot['idx']}: видео {seconds} с", "provider": provider, "model": model,
            "resolution": resolution, "aspect": "9:16", "seconds": seconds, "start": start,
            "estimate_rub": price["rub"], "price_note": price["note"]}


def plan_stage(store: Any, project_id: int, stage: str, script_id: int | None = None, items: list | None = None,
               regenerate: bool = False) -> list[dict]:
    """Список запусков этапа (без побочных эффектов). Без ``items`` — всё, чего ещё нет или
    что устарело; с ``items`` — только они (перегенерация — новая версия)."""
    from app.aivideo_providers import get_tts

    ext = EXTENSIONS.get(str(stage or ""))
    if ext is not None:
        V.require_project(store, project_id)
        planned = ext.plan(store, project_id, list(items or []), regenerate)
        for item in planned:
            item.setdefault("script_id", None)
            item["regenerate"] = bool(regenerate or item.get("regenerate"))
        return planned
    stage = P._choice(stage, STAGES, "stage")
    style = V.get_style(store, project_id)
    provider, model = style["provider"], style["image_model"]
    quality = _quality(store, project_id, script_id)
    if stage in ("blocking", "keyframes", "voice", "video"):
        if script_id is None:
            raise V.AivError(f"этап {stage} — для серии: нужен script_id")
        script = V.get_script(store, script_id)
        if int(script["project_id"]) != int(project_id):
            raise V.AivError("серия из другого проекта")
        if not script["shots"]:
            raise V.AivError("в серии нет планов — сначала сценарий (aiv_write_script / aiv_update_script)")
    wanted = {_parse_item(i) for i in items or []}
    explicit = bool(wanted)

    def want(target_type: str, target_id: int, kind: str, slot: str = "") -> bool | None:
        if not explicit:
            return None
        return any(t == target_type and i == target_id and (not k or k == kind) and (not s or s == slot)
                   for t, i, k, s in wanted)

    def need(ref: dict | None, target_type: str, target_id: int, kind: str, slot: str = "") -> bool:
        hit = want(target_type, target_id, kind, slot)
        if hit is not None:
            return hit
        return regenerate or not _fresh(ref)

    out: list[dict] = []
    if stage == "refs":
        for n in range(1, STYLE_REF_COUNT + 1):
            ref = V.selected_ref(store, project_id, "style", 0, "style", str(n))
            if need(ref, "style", 0, "style", str(n)):
                out.append(_image_item(stage, "style", 0, "style", f"стиль {n}", model, quality, provider, str(n)))
        for loc in V.list_locations(store, project_id):
            ref = V.selected_ref(store, project_id, "location", loc["id"], "location")
            if need(ref, "location", loc["id"], "location"):
                out.append(_image_item(stage, "location", loc["id"], "location", f"локация «{loc['name']}»", model,
                                       quality, provider))
        for c in V.list_characters(store, project_id):
            for kind in ("turnaround", "expressions"):
                ref = V.selected_ref(store, project_id, "character", c["id"], kind)
                if need(ref, "character", c["id"], kind):
                    out.append(_image_item(stage, "character", c["id"], kind, f"{c['name']}: {REF_LABELS[kind]}",
                                           model, quality, provider))
            # Облик — img2img-вариант листа: база — разворот (или картинки владельца) персонажа.
            turn = V.selected_ref(store, project_id, "character", c["id"], "turnaround")
            base = _fresh(turn) or any(i["target_type"] == "character" and i["target_id"] == c["id"]
                                       and i["ref_kind"] == "turnaround" for i in out) or bool(
                V.list_refs(store, project_id, "character", c["id"], "upload"))
            for look in c["looks"]:
                ref = V.selected_ref(store, project_id, "look", look["id"], "look")
                if need(ref, "look", look["id"], "look"):
                    item = _image_item(stage, "look", look["id"], "look", f"{c['name']}: облик «{look['name']}»",
                                       model, quality, provider)
                    if not base:
                        item["blocked"] = (f"облик «{look['name']}» — правка листа {c['name']}: сначала разворот "
                                           f"(character:{c['id']}:turnaround)")
                    out.append(item)
    elif stage == "blocking":
        # 3D-макет плана (старт и, у плана с видео, конец) — 0 ₽, компоновщиком.
        from app import aivideo_blocking as B

        for shot in script["shots"]:
            if shot["locked"] and not explicit:
                continue
            todo = B.needs_render(store, project_id, shot)
            for which in ("start", "end") if shot["needs_motion"] else ("start",):
                hit = want("shot", shot["id"], f"blocking_{which}")
                if hit is False or (hit is None and which not in todo and not regenerate):
                    continue
                out.append({"stage": stage, "kind": "blocking", "target_type": "shot", "target_id": int(shot["id"]),
                            "ref_kind": f"blocking_{which}", "slot": "", "script_id": int(script_id),
                            "label": f"план {shot['idx']}: блокинг {'старт' if which == 'start' else 'конец'}",
                            "provider": "none", "model": "scene", "estimate_rub": 0.0,
                            "price_note": "блокинг — наш движок сцен, 0 ₽"})
    elif stage == "keyframes":
        # Пары ключевых кадров по порядку: старт плана (если он не общий с концом прошлого) и конец
        # плана с видео. Конец плана N = старт сцепленного плана N+1 — один кадр на двоих (N+1 кадров
        # на цепочку из N планов), ничего не вынимается из видео — ошибки не копятся.
        # Видеомодель без конечного кадра (H3, Grok): конечные кадры не заказываем — видео к ним не
        # приходит; старт продолжения — последний кадр готового видео прошлого плана (после этапа video).
        shots = script["shots"]
        tail = tail_mode(store, project_id, script_id)
        for n, shot in enumerate(shots):
            if shot["locked"] and not explicit:
                continue
            prev = shots[n - 1] if n else None
            pair = bool(shot["needs_motion"])
            if _is_chain(shot, prev) and tail:
                out.append({**_share_item(shot, prev, script["id"]),
                            "label": f"план {shot['idx']}: старт — последний кадр видео плана {prev['idx']}",
                            "skipped": f"план {shot['idx']}: старт — последний кадр видео плана {prev['idx']} "
                                       "(после этапа video, 0 ₽)"})
            elif _is_chain(shot, prev):
                out.append({**_share_item(shot, prev, script["id"]),
                            "skipped": f"план {shot['idx']}: старт — конечный кадр плана {prev['idx']} (общий кадр, "
                                       "0 ₽)"})
            else:
                ref = V.selected_ref(store, project_id, "shot", shot["id"], "keyframe")
                if need(ref, "shot", shot["id"], "keyframe"):
                    item = _image_item(stage, "shot", shot["id"], "keyframe",
                                       f"план {shot['idx']}: старт" if pair else f"план {shot['idx']}", model,
                                       quality, provider)
                    out.append({**item, "script_id": int(script_id)})
            if pair and (not tail or want("shot", shot["id"], "endframe")):
                ref = V.selected_ref(store, project_id, "shot", shot["id"], "endframe")
                if need(ref, "shot", shot["id"], "endframe"):
                    item = _image_item(stage, "shot", shot["id"], "endframe", f"план {shot['idx']}: конец", model,
                                       quality, provider)
                    out.append({**item, "script_id": int(script_id)})
    elif stage == "video":
        out += _plan_video(store, project_id, script, style, explicit, want, need)
    else:  # voice
        for shot in script["shots"]:
            for i, line in enumerate(shot["dialogue"]):
                if speech_of(store, project_id, line["who"])[1] == "none":
                    continue  # молчит — реплики нет (normalize_shot их уже убирает)
                voice = _voice_of(store, project_id, line["who"], line.get("emotion") or "")
                ref = V.selected_ref(store, project_id, "line", shot["id"], "line_audio", str(i))
                current = _fresh(ref) and ref["prompt"] == _line_key(voice, line["text"])
                hit = want("line", shot["id"], "line_audio", str(i))
                if hit is False or (hit is None and current and not regenerate):
                    continue
                if voice["model"] == "sfx":
                    out.append({"stage": stage, "kind": "sfx", "target_type": "line", "target_id": int(shot["id"]),
                                "ref_kind": "line_audio", "slot": str(i), "script_id": int(script_id),
                                "label": f"план {shot['idx']}, звук {i + 1} ({line['who']})", "provider": "none",
                                "model": "sfx", "voice": voice["voice"], "text": line["text"], "chars": 0,
                                "estimate_rub": 0.0, "price_note": "звук пака (запись), 0 ₽"})
                    continue
                tts = get_tts(voice["provider"], voice["model"], voice["voice"], voice.get("style"))
                price = tts.price_rub_per_char() if getattr(tts, "paid", False) else 0.0
                free_note = "Gemini TTS, бесплатно" if voice["provider"] == "gemini" else "сухой прогон"
                out.append({"stage": stage, "kind": "tts", "target_type": "line", "target_id": int(shot["id"]),
                            "ref_kind": "line_audio", "slot": str(i), "script_id": int(script_id),
                            "label": f"план {shot['idx']}, реплика {i + 1} ({line['who']})",
                            "provider": voice["provider"], "model": voice["model"], "voice": voice["voice"],
                            **({"style": voice["style"]} if voice.get("style") else {}),
                            "text": line["text"], "chars": len(line["text"]),
                            "estimate_rub": round(len(line["text"]) * price, 2),
                            "price_note": free_note if not price else "цена TTS из app/dubbing.py"})
    for item in out:
        item.setdefault("script_id", script_id)
        item["regenerate"] = bool(regenerate or explicit)
    return out


def _is_chain(shot: dict, prev: dict | None) -> bool:
    """План продолжает прошлый план с видео: его старт — конечный кадр прошлого (один общий кадр)."""
    return bool(prev and shot.get("continues_from_prev") and prev.get("needs_motion"))


def _fresh_video(store: Any, project_id: int, shot: dict | None) -> dict | None:
    if not shot:
        return None
    ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), "shot_video")
    return ref if _fresh(ref) else None


def _usable(ref: dict | None) -> dict | None:
    return ref if ref and ref.get("asset_id") else None


TAIL_SOURCE = "source=video_tail"  # пометка версии keyframe: старт — последний кадр готового видео прошлого плана


def series_video_model(store: Any, project_id: int, script_id: int | None) -> tuple[str, str]:
    """(видеомодель, провайдер видео) серии."""
    style = V.get_style(store, project_id)
    quality = V.get_script(store, int(script_id), with_shots=False)["quality"] if script_id is not None else None
    return video_model_for(style, quality), style["video_provider"]


def tail_mode(store: Any, project_id: int, script_id: int | None) -> bool:
    """Видеомодель серии НЕ берёт конечный кадр (H3, Grok — только старт): видео плана к нарисованному
    концу не приходит, поэтому старт продолжения — ПОСЛЕДНИЙ кадр готового видео прошлого плана, а
    конечные кадры не заказываются (деньги зря)."""
    model, provider = series_video_model(store, project_id, script_id)
    return not video_capabilities(model, provider).get("first_last")


def _tail_marker(video_ref: dict) -> str:
    return f"{TAIL_SOURCE}; video_ref={int(video_ref['id'])}"


def tail_start_ref(store: Any, project_id: int, prev: dict, shot: dict) -> dict | None:
    """Старт сцепленного плана из хвоста видео прошлого: версия keyframe с пометкой video_tail ИМЕННО
    от выбранной сейчас версии видео прошлого плана (новое видео — старый хвост не годится)."""
    video = _usable(V.selected_ref(store, project_id, "shot", int(prev["id"]), "shot_video"))
    if video is None:
        return None
    marker = _tail_marker(video)
    for ref in reversed(V.list_refs(store, project_id, "shot", int(shot["id"]), "keyframe")):
        if ref.get("prompt") == marker and ref.get("asset_id") and _asset_path(store, ref) is not None:
            if not ref["selected"]:
                ref = V.select_ref(store, int(ref["id"]))
            return ref
    return None


def make_tail_start(store: Any, project_id: int, prev: dict, shot: dict, job: dict | None = None) -> dict | None:
    """Вынуть последний кадр готового видео прошлого плана (ffmpeg) → версия keyframe плана ``shot``
    с пометкой ``source=video_tail``. Уже есть от этого же видео — она. Видео нет — None."""
    have = tail_start_ref(store, project_id, prev, shot)
    if have is not None:
        return have
    video = _usable(V.selected_ref(store, project_id, "shot", int(prev["id"]), "shot_video"))
    frame = video_last_frame(store, video) if video else None
    if frame is None:
        return None
    from app.montage_assets import assets_dir, probe_asset

    dest = assets_dir() / f"{uuid4().hex}-aiv-keyframe-tail.jpg"
    shutil.copyfile(frame, dest)
    meta = probe_asset(dest)
    asset = store.create_montage_asset(
        kind="image", label=f"ИИ-видео: план {shot['idx']} — старт из хвоста видео плана {prev['idx']}"[:120],
        description=(f"ИИ-видео: старт плана {shot['idx']} — последний кадр видео плана {prev['idx']} "
                     f"({TAIL_SOURCE})")[:500],
        tags="ии-видео", file_path=str(dest), original_filename=dest.name, mime_type="image/jpeg",
        size_bytes=dest.stat().st_size, **meta)
    ref = V.add_ref(store, project_id, "shot", int(shot["id"]), "keyframe", int(asset["id"]),
                    prompt=_tail_marker(video), job_id=(job or {}).get("id"))
    # Новый старт — видео этого плана (если было) стоит на старом старте: устарело.
    store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? AND kind = "
                     "'shot_video'", (int(shot["id"]),))
    return ref


def start_frame_ref(store: Any, project_id: int, shots: list[dict], n: int) -> tuple[dict | None, int | None]:
    """Стартовый кадр плана ``shots[n]`` и откуда он: (версия, idx плана, чей это КОНЕЦ — или None,
    если кадр свой). Сцепленный план берёт конечный кадр прошлого плана — тот же файл; если видеомодель
    серии конечный кадр не берёт — последний кадр готового видео прошлого плана (``source=video_tail``)."""
    shot = shots[n]
    prev = shots[n - 1] if n else None
    if _is_chain(shot, prev):
        if tail_mode(store, project_id, shot.get("script_id")):
            return tail_start_ref(store, project_id, prev, shot), int(prev["idx"])
        return _usable(V.selected_ref(store, project_id, "shot", int(prev["id"]), "endframe")), int(prev["idx"])
    return _usable(V.selected_ref(store, project_id, "shot", int(shot["id"]), "keyframe")), None


def end_frame_ref(store: Any, project_id: int, shot: dict) -> dict | None:
    """Конечный кадр плана с видео (у статичного — нет: его кадр не меняется)."""
    if not shot.get("needs_motion"):
        return None
    return _usable(V.selected_ref(store, project_id, "shot", int(shot["id"]), "endframe"))


def _share_item(shot: dict, prev: dict, script_id: int) -> dict:
    return {"stage": "keyframes", "kind": "share", "target_type": "shot", "target_id": int(shot["id"]),
            "ref_kind": "keyframe", "slot": "", "script_id": int(script_id),
            "label": f"план {shot['idx']}: старт — конечный кадр плана {prev['idx']}", "provider": "none",
            "model": "", "estimate_rub": 0.0, "price_note": "0 ₽ — общий кадр"}


def video_model_for(style: dict, quality: str | None) -> str:
    """Видеомодель серии: заданная в проекте, иначе AIVIDEO_VIDEO_MODEL, иначе по режиму серии."""
    if style.get("video_model_set"):
        return style["video_model"]
    return settings.aivideo_video_model or default_video_model(quality)


def _plan_video(store: Any, project_id: int, script: dict, style: dict, explicit: bool, want: Any,
                need: Any) -> list[dict]:
    """Видео планов с needs_motion. Старт — стартовый кадр плана (свой или конечный кадр прошлого),
    конец — конечный кадр плана; модель с первым+последним кадром ИНТЕРПОЛИРУЕТ между ними. Нет
    старта (или конца у такой модели) — элемент ``blocked``: сначала этап keyframes."""
    provider, res = style["video_provider"], style["video_resolution"]
    model = video_model_for(style, script.get("quality"))
    caps = video_capabilities(model, provider)
    out: list[dict] = []
    shots = script["shots"]
    for n, shot in enumerate(shots):
        if not shot["needs_motion"]:
            continue
        if shot["locked"] and not explicit:
            continue
        ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), "shot_video")
        if not need(ref, "shot", int(shot["id"]), "shot_video"):
            continue
        start, shared = start_frame_ref(store, project_id, shots, n)
        end = end_frame_ref(store, project_id, shot)
        tail = bool(shared) and not caps.get("first_last")
        item = _video_item(shot, script["id"], provider, model, res,
                           "video_tail" if tail else ("shared" if shared else "keyframe"))
        item["mode"] = choose_video_mode(model, end is not None, provider=provider)
        if start is None and tail:
            # Старт — хвост видео прошлого плана: есть видео, оно в этой же партии или уже в работе —
            # запуск ждёт его в очереди и вынимает последний кадр сам.
            prev = shots[n - 1]
            ready = _usable(V.selected_ref(store, project_id, "shot", int(prev["id"]), "shot_video"))
            planned = any(i["target_id"] == int(prev["id"]) and not i.get("blocked") for i in out)
            if ready or planned or _video_busy(store, int(prev["id"])):
                item["after"] = int(prev["id"])
                out.append(item)
            else:
                out.append({**item, "blocked": f"план {shot['idx']}: старт — последний кадр видео плана "
                                                f"{prev['idx']}: сначала видео плана {prev['idx']}"})
            continue
        if start is None:
            where = f"конечного кадра плана {shared}" if shared else "стартового кадра"
            out.append({**item, "blocked": f"план {shot['idx']}: нет {where} — сначала этап keyframes"})
            continue
        if caps.get("first_last") and end is None:
            out.append({**item, "blocked": f"план {shot['idx']}: нет конечного кадра — этап keyframes (модель "
                                            "интерполирует старт → конец)"})
            continue
        out.append(item)
    return out


def _video_busy(store: Any, shot_id: int) -> bool:
    marks = ", ".join("?" for _ in BUSY)
    return bool(store.db.query_one(
        f"SELECT id FROM aiv_gen_jobs WHERE kind = 'video' AND target_type = 'shot' AND target_id = ? "
        f"AND status IN ({marks}) LIMIT 1", (int(shot_id), *BUSY)))


def estimate(store: Any, project_id: int, stage: str, script_id: int | None = None, items: list | None = None,
             regenerate: bool = False) -> dict:
    """Оценка этапа (бесплатно, ничего не запускает)."""
    V.require_project(store, project_id)
    raw = plan_stage(store, project_id, stage, script_id, items, regenerate)
    planned = [i for i in raw if not i.get("blocked") and not i.get("skipped")]
    blocked = [i["blocked"] for i in raw if i.get("blocked")]
    notes = [i["skipped"] for i in raw if i.get("skipped")]
    # Ждущая карточка этого же этапа, которую новая заменит (request_stage): её запуски не «в работе»
    # и не резерв — иначе повторный запрос того же этапа упирается в потолок сам о себя.
    replaces = (_replaceable_cards(store, project_id, stage, script_id, planned[0].get("group"))
                if planned and not planned[0].get("regenerate") else [])
    busy_jobs: list[dict] = []
    if not regenerate:
        # Повтор запроса (двойной клик, повтор после ошибки клиента): цель, у которой уже есть
        # ждущий/идущий запуск, второй раз не заказываем — иначе кадр рисуется и оплачивается дважды.
        # Явная перегенерация (aiv_regenerate, regenerate=true) — как раньше: новая версия.
        busy = _busy_jobs(store, project_id, exclude_cards=replaces)
        kept = []
        for item in planned:
            hit = busy.get(_target_key(item))
            if hit is None:
                kept.append(item)
                continue
            busy_jobs.append(hit)
            notes.append(f"{item['label']}: уже в работе (запуск #{hit['id']}, {hit['status']}"
                         + (f", карточка #{hit['approval_id']}" if hit.get("approval_id") else "")
                         + ") — второй раз не заказываю"
                         + ("; держится — «Продолжить» у карточки (aiv_run_approved) или отмени её запуски"
                            if hit["status"] == "held" else ""))
        planned = kept
    total = round(sum(i["estimate_rub"] for i in planned), 2)
    paid = any(_paid(i["provider"], i["kind"]) for i in planned)
    problems = cap_problems(store, project_id, script_id, total, stage, exclude_approval=replaces) if paid else []
    problems += blocked
    if paid and not settings.aivideo_enabled:
        problems.append("платная генерация выключена (AIVIDEO_ENABLED=0) — включает владелец; карточку можно "
                        "отправить заранее, запуск — после включения")
    style = V.get_style(store, project_id)
    extra: dict[str, Any] = {}
    if stage == "video":
        provider = style["video_provider"]
        quality = V.get_script(store, script_id, with_shots=False)["quality"] if script_id is not None else None
        model = video_model_for(style, quality)
        caps = video_capabilities(model, provider)
        modes = sorted({i.get("mode") or "" for i in planned} - {""})
        extra = {"video_caps": caps, "modes": modes}
        if planned:
            notes.append(f"{model}: " + ", ".join(VIDEO_MODE_LABELS.get(m, m) for m in modes)
                         + ("" if not caps.get("exclusive") else " (кадры и листы персонажей вместе не шлются — "
                                                                 "у модели это разные режимы)"))
    else:
        provider, model = style["provider"], style["image_model"]
    if stage == "voice" or (planned and all(i["kind"] == "tts" for i in planned)):
        model = "TTS"
    elif stage in EXTENSIONS and any(i["kind"] == "tts" for i in planned):
        model = f"{model} + TTS"
    if stage == "blocking":
        provider, model = "none", "scene"
    if stage == "keyframes" and script_id is not None:
        pid = int(project_id)
        no_block = [s["idx"] for s in V.get_script(store, script_id)["shots"]
                    if not V.selected_ref(store, pid, "shot", int(s["id"]), "blocking_start")]
        if no_block:
            notes.append("нет блокинга у планов " + ", ".join(map(str, no_block)) + " — кадры нарисуются без "
                         "3D-макета композиции; сначала этап blocking (0 ₽)")
        # Лимит референсов модели (nano-banana 10): блокинг + кадр-основа + листы + плита + стиль.
        over = []
        for item in planned:
            try:
                info = build_request(store, {**item, "project_id": pid})[3]
            except Exception:  # noqa: BLE001 - оценка не падает из-за подсказки
                continue
            cut = [m.split(": ", 1)[1] for m in info["missing"] if m.startswith("не влезло")]
            if cut:
                over.append(f"{item['label']}: не влезут {', '.join(cut)}")
        if over:
            notes.append(f"лимит референсов {max_refs(model, provider)} у {model}: " + "; ".join(over[:4]))
        pairs = sum(1 for i in planned if i["ref_kind"] == "endframe")
        if pairs:
            notes.append(f"пары кадров: {pairs} конечных; конец плана = старт сцепленного следующего (один кадр)")
        if tail_mode(store, pid, script_id):
            vmodel = series_video_model(store, pid, script_id)[0]
            notes.append(f"конечные кадры не заказываю: {vmodel} берёт только стартовый кадр — видео к нарисованному "
                         "концу не приходит; старт продолжения — последний кадр готового видео прошлого плана")
    note = (f"{STAGE_LABELS[stage]}: {len(planned)} шт. ≈ {total:.2f} ₽"
            + (f" ({model}, цены от {PRICE_CHECKED}: "
               + ", ".join(sorted({i['price_note'] for i in planned if _paid(i['provider'], i['kind'])})) + ")"
               if paid else " — бесплатно (сухой прогон / Gemini TTS), 0 ₽"))
    if stage == "video" and paid and any("потолка серии" in p for p in problems):
        problems.append(f"подсказка: серия-черновик ≤ {EPISODE_CAP_RUB['draft']:.0f} ₽ — для видео переключи серию "
                        "в quality (aiv_update_script quality) или возьми дешёвую модель (video_model "
                        "grok-imagine-video-1-5); решает владелец")
    out = {"stage": stage, "stage_label": STAGE_LABELS[stage], "project_id": int(project_id), "script_id": script_id,
           "items": planned, "count": len(planned), "total_rub": total, "paid": paid, "blocked": blocked,
           "notes": notes, "continuity": continuity_flags(store, script_id) if script_id is not None else [],
           "provider": provider, "model": model, "problems": problems, "busy": busy_jobs, "replaces": replaces,
           "budget": budget_view(store, project_id, script_id), "note": note, **extra}
    if stage == "video" and script_id is not None:
        out["video_qa"] = video_qa_flags(store, script_id)
    return out


# --------------------------------------------------------------------------- #
# Запрос этапа → карточка владельцу
# --------------------------------------------------------------------------- #


# Запуск на цели «занят»: ждёт одобрения, одобрен/держится или идёт у провайдера.
BUSY = ("awaiting", "held") + ACTIVE
_REQUEST_LOCK = threading.Lock()  # два одновременных запроса этапа (двойной клик) — по очереди


def _target_key(item: dict) -> tuple[str, int, str, str]:
    return (str(item["target_type"]), int(item["target_id"]), str(item["ref_kind"]), str(item.get("slot") or ""))


def _busy_jobs(store: Any, project_id: int, exclude_cards: Any = ()) -> dict[tuple, dict]:
    """Цель → её занятый запуск (BUSY), кроме запусков карточек ``exclude_cards`` (их заменит новая)."""
    marks = ", ".join("?" for _ in BUSY)
    rows = store.db.query_all(
        f"SELECT id, approval_id, status, target_type, target_id, ref_kind, slot FROM aiv_gen_jobs "
        f"WHERE project_id = ? AND status IN ({marks}) ORDER BY id", (int(project_id), *BUSY))
    skip = {int(x) for x in exclude_cards}
    out: dict[tuple, dict] = {}
    for row in rows:
        if row["approval_id"] is not None and int(row["approval_id"]) in skip:
            continue
        out.setdefault(_target_key(dict(row)), dict(row))
    return out


def _replaceable_cards(store: Any, project_id: int, stage: str, script_id: int | None, group: Any) -> list[int]:
    """Ждущие карточки этого этапа (та же серия и группа) — новая карточка этапа заменяет их."""
    return [int(c["id"]) for c in stage_approvals(store, project_id, script_id, status="pending")
            if c["stage"] == stage and c.get("script_id") == script_id and c.get("group") == group]


def _card_keys(store: Any, approval_id: int) -> list[tuple]:
    return sorted(_target_key(dict(r)) for r in store.db.query_all(
        "SELECT target_type, target_id, ref_kind, slot FROM aiv_gen_jobs WHERE approval_id = ? AND status = 'awaiting'",
        (int(approval_id),)))


def _insert_job(store: Any, project_id: int, item: dict, status: str, approval_id: int | None,
                requested_by: str) -> int:
    params = {"resolution": item.get("resolution"), "aspect": item.get("aspect"), "voice": item.get("voice"),
              "text": item.get("text"), "style": item.get("style"), "regenerate": item.get("regenerate", False),
              "seconds": item.get("seconds"),
              "start": item.get("start"), "mode": item.get("mode"), "group": item.get("group")}
    cur = store.db.execute(
        "INSERT INTO aiv_gen_jobs (project_id, script_id, approval_id, stage, kind, target_type, target_id, ref_kind, "
        "slot, label, provider, model, params_json, estimate_rub, status, requested_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(project_id), item.get("script_id"), approval_id, item["stage"], item["kind"], item["target_type"],
         int(item["target_id"]), item["ref_kind"], item.get("slot") or "", item["label"][:200], item["provider"],
         item["model"], json.dumps({k: v for k, v in params.items() if v is not None}, ensure_ascii=False),
         float(item["estimate_rub"]), status, str(requested_by or "")[:40]))
    return int(cur.lastrowid)


def _card_question(store: Any, project: dict, est: dict, script: dict | None) -> str:
    bud = est["budget"]
    ext = EXTENSIONS.get(est["stage"])
    head = ext.card_line(store, project["id"], est) if ext is not None and hasattr(ext, "card_line") else ""
    lines = [head or (f"ИИ-видео «{project['name']}»"
                      + (f", серия {script['episode_no']} «{script['title']}»" if script else "")
                      + f": {est['stage_label']} — {est['count']} шт. ≈ {est['total_rub']:.2f} ₽ "
                      f"({est['model'] if est['stage'] != 'voice' else 'TTS'}).")]
    month = bud["month"]
    lines.append(f"Месяц проекта: потрачено {month['spent_rub']:.0f} из {month['cap_rub']:.0f} ₽"
                 + (f"; серия: {bud['episode']['spent_rub']:.0f} из {bud['episode']['cap_rub']:.0f} ₽"
                    if bud.get("episode") else "") + ".")
    names = [i["label"] for i in est["items"][:8]]
    lines.append("Что: " + "; ".join(names) + (f" и ещё {est['count'] - 8}" if est["count"] > 8 else ""))
    bad = [f for f in est.get("continuity") or [] if not f["ok"] and not f["outdated"]]
    if bad:
        lines.append("⚠ Склейки (Gemini): " + "; ".join(
            f"план {f['idx']} — " + (", ".join(m["detail"] for m in f["mismatches"][:2]) or f"оценка {f['score']}")
            for f in bad[:4]) + ". Можно сначала перерисовать эти кадры (дёшево).")
    if est["stage"] == "video" and est.get("modes"):
        lines.append("Режим: " + ", ".join(VIDEO_MODE_LABELS.get(m, m) for m in est["modes"]) + ".")
    targets = {int(i["target_id"]) for i in est["items"]}
    qa = [f for f in est.get("video_qa") or [] if not f["ok"] and (est["stage"] != "video" or f["shot_id"] in targets)]
    if qa:
        lines.append("⚠ Проверка видео (Gemini): " + "; ".join(
            f"план {f['idx']} — " + ("; ".join(i["detail"] for i in f["issues"][:2]) or f"оценка {f['score']}")
            for f in qa[:4]) + ".")
    if not settings.aivideo_enabled:
        lines.append("⚠ AIVIDEO_ENABLED=0 — запустится после включения владельцем.")
    lines.append("Разрешить эту сумму?")
    return "\n".join(lines)


def request_stage(store: Any, project_id: int, stage: str, script_id: int | None = None, items: list | None = None,
                  regenerate: bool = False, requested_by: str = "agent", note: str = "") -> dict:
    """Этап: сухой прогон — сразу (0 ₽); платно — карточка владельцу с суммой (жди решения)."""
    project = V.require_project(store, project_id)
    continuity = None
    if stage == "video" and script_id is not None:
        # Бесплатная проверка склеек ДО платного видео: несоответствия — на раскадровку и в карточку.
        try:
            continuity = check_continuity(store, int(script_id))
        except Exception as exc:  # noqa: BLE001 - Gemini недоступен: этап не блокируем
            continuity = {"ok": False, "skipped": True, "note": f"проверка склеек не удалась: {exc}"[:300]}
    with _REQUEST_LOCK:
        res = _request_locked(store, project, stage, script_id, items, regenerate, requested_by, note, continuity)
    if res["status"] == "run":  # сухой прогон / бесплатное: запуски уже в базе — выполняем вне замка
        res = {"ok": True, "status": "done", "estimate": res["estimate"], "run": run_jobs(store, res["job_ids"]),
               "note": "сухой прогон: заглушки вместо картинок, тон вместо голоса, 0 ₽"}
    return res


def _request_locked(store: Any, project: dict, stage: str, script_id: int | None, items: list | None,
                    regenerate: bool, requested_by: str, note: str, continuity: dict | None) -> dict:
    project_id = int(project["id"])
    est = estimate(store, project_id, stage, script_id, items, regenerate)
    if continuity is not None:
        est["continuity_check"] = continuity
    if est["blocked"]:
        raise V.AivError("этап не запрошен: " + "; ".join(est["blocked"]))
    if not est["items"]:
        if est["busy"]:
            cards = sorted({int(j["approval_id"]) for j in est["busy"] if j.get("approval_id")})
            out = {"ok": True, "status": "in_progress", "estimate": est,
                   "job_ids": [int(j["id"]) for j in est["busy"]],
                   "note": "уже заказано и ждёт решения или идёт — второй раз не заказываю"
                           + (" (карточка #" + ", #".join(map(str, cards)) + ")" if cards else "")}
            if len(cards) == 1:
                out["approval_id"] = cards[0]
            return out
        return {"ok": True, "status": "nothing", "estimate": est,
                "note": "всё уже есть и не устарело — для новой версии укажи items (перегенерация)"}
    if not est["paid"]:
        job_ids = [_insert_job(store, project_id, item, "approved", None, requested_by) for item in est["items"]]
        return {"status": "run", "estimate": est, "job_ids": job_ids}
    hard = [p for p in est["problems"] if "AIVIDEO_ENABLED" not in p]
    if hard:
        raise BudgetError("этап не запрошен: " + "; ".join(hard))
    script = V.get_script(store, script_id, with_shots=False) if script_id is not None else None
    # Группа карточки (расширения: один ролик-объяснялка) — заменяется только карточка той же группы.
    group = est["items"][0].get("group")
    # Тот же запрос второй раз (двойной клик, повтор после ошибки клиента), а ждущая карточка с теми же
    # запусками и суммой уже есть — она и есть ответ: вторую карточку владельцу не шлём.
    want = sorted(_target_key(i) for i in est["items"])
    for old_id in est["replaces"]:
        old = A.get_approval(store, old_id)
        if (_card_keys(store, old_id) == want
                and abs(float((old.get("payload") or {}).get("amount_rub") or 0) - est["total_rub"]) < 0.01):
            return {"ok": True, "status": "awaiting_approval", "approval_id": old_id, "estimate": est,
                    "duplicate": True,
                    "note": f"карточка #{old_id} на {est['total_rub']:.2f} ₽ уже ждёт владельца — вторую не шлю"}
    # Прежняя ждущая карточка того же этапа — устарела: новая заменяет её.
    for old_id in est["replaces"]:
        A._update(store, old_id, status="expired", decision_comment="заменена новой карточкой этапа")
        store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'карточка заменена' "
                         "WHERE approval_id = ? AND status = 'awaiting'", (old_id,))
    payload = {"action": CARD_ACTION, "stage": stage, "project_id": int(project_id), "script_id": script_id,
               "amount_rub": est["total_rub"], "count": est["count"], "model": est["model"],
               "provider": est["provider"], "note": P._text(note, 300)}
    if group:
        payload["group"] = group
    prefix = getattr(EXTENSIONS[stage], "card_prefix", "Объяснялка") if stage in EXTENSIONS else "ИИ-видео"
    card = A.submit_card(store, project_id, "other", _card_question(store, project, est, script), payload,
                         title=f"{prefix}: {est['stage_label']} ≈ {est['total_rub']:.0f} ₽",
                         submitted_by=requested_by)
    job_ids = [_insert_job(store, project_id, item, "awaiting", card["id"], requested_by) for item in est["items"]]
    store.db.execute("UPDATE approvals SET payload_json = ? WHERE id = ?",
                     (json.dumps({**payload, "job_ids": job_ids}, ensure_ascii=False), card["id"]))
    return {"ok": True, "status": "awaiting_approval", "approval_id": card["id"], "estimate": est,
            "note": (f"карточка #{card['id']} на {est['total_rub']:.2f} ₽ ушла владельцу (UI «Одобрение», страница "
                     "проекта, Telegram). Запуск — только после его «Разрешить»; сам не одобряй и не обходи.")}


def regenerate(store: Any, ref_id: int | None = None, target: Any = None, requested_by: str = "agent",
               note: str = "") -> dict:
    """Новая версия одной картинки/реплики: своя маленькая оценка и своя карточка."""
    if ref_id is not None:
        ref = V.get_ref(store, ref_id)
        target_type, target_id, kind, slot = ref["target_type"], int(ref["target_id"]), ref["kind"], ref["slot"]
        project_id = int(ref["project_id"])
    else:
        target_type, target_id, kind, slot = _parse_item(target)
        project_id = _project_of_target(store, target_type, target_id)
    if kind == "upload":
        raise V.AivError("загрузку владельца не перегенерируют — это его референс")
    stage, script_id = _stage_of(store, target_type, target_id, kind)
    item = {"target_type": target_type, "target_id": target_id, "kind": kind, "slot": slot}
    return request_stage(store, project_id, stage, script_id, [item], regenerate=True, requested_by=requested_by,
                         note=note)


def _project_of_target(store: Any, target_type: str, target_id: int) -> int:
    ext = _ext_of_target(target_type)
    if ext is not None:
        return int(ext.project_of(store, target_type, target_id))
    if target_type == "character":
        return int(V.get_character(store, target_id)["project_id"])
    if target_type == "look":
        return int(V.get_character(store, V.get_look(store, target_id)["character_id"])["project_id"])
    if target_type == "location":
        return int(V.get_location(store, target_id)["project_id"])
    if target_type in ("shot", "line"):
        return int(V.get_script(store, V.get_shot(store, target_id)["script_id"], with_shots=False)["project_id"])
    raise V.AivError("для стиля нужен ref_id или project_id")


def _stage_of(store: Any, target_type: str, target_id: int, kind: str) -> tuple[str, int | None]:
    ext = _ext_of_target(target_type)
    if ext is not None:
        return ext.stage, None
    if target_type == "shot":
        stage = "video" if kind == "shot_video" else "blocking" if kind.startswith("blocking") else "keyframes"
        return stage, int(V.get_shot(store, target_id)["script_id"])
    if target_type == "line":
        return "voice", int(V.get_shot(store, target_id)["script_id"])
    return "refs", None


def stage_approvals(store: Any, project_id: int, script_id: int | None = None, status: str | None = None) -> list[dict]:
    """Карточки этапов проекта (последние первыми): сумма, этап, статус, сколько уже потрачено."""
    sql = "SELECT id FROM approvals WHERE project_id = ? AND kind = 'other' AND payload_json LIKE ?"
    params: list[Any] = [int(project_id), f'%"action": "{CARD_ACTION}"%']
    if status:
        sql += " AND status = ?"
        params.append(status)
    rows = store.db.query_all(sql + " ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC LIMIT 50", params)
    out = []
    for r in rows:
        card = A.get_approval(store, int(r["id"]))
        payload = card.get("payload") or {}
        if script_id is not None and payload.get("script_id") not in (None, script_id):
            continue
        counts = {row["status"]: int(row["n"]) for row in store.db.query_all(
            "SELECT status, COUNT(*) AS n FROM aiv_gen_jobs WHERE approval_id = ? GROUP BY status", (card["id"],))}
        # Сеть: точно не оплачено — «Повторить» без новой карточки (retry_card).
        retryable = sum(1 for row in store.db.query_all(
            "SELECT params_json FROM aiv_gen_jobs WHERE approval_id = ? AND status IN ('failed', 'uncertain') "
            "AND remote_id = ''", (card["id"],)) if V._loads(row["params_json"], {}).get("retry_free"))
        out.append({"id": card["id"], "status": card["status"], "stage": payload.get("stage"),
                    "group": payload.get("group"),
                    "script_id": payload.get("script_id"), "amount_rub": payload.get("amount_rub"),
                    "count": payload.get("count"), "model": payload.get("model"), "title": card["title"],
                    "question": card["question"], "decided_by": card.get("decided_by"),
                    "decision_comment": card.get("decision_comment"), "created_at": card.get("created_at"),
                    "spent_rub": spent_approval(store, card["id"]), "jobs": counts, "retryable": retryable})
    return out


def _on_decide(store: Any, approval: dict, action: str, comment: str, actor: str) -> dict:
    if action in ("approve", "both"):
        # Цель, которую уже делает другая одобренная карточка (запуск одобрен/держится/идёт), второй
        # раз не платим: такой запуск этой карточки — «дубль» (явная перегенерация — не трогаем).
        marks = ", ".join("?" for _ in ("held",) + ACTIVE)
        dupes = 0
        for row in store.db.query_all("SELECT * FROM aiv_gen_jobs WHERE approval_id = ? AND status = 'awaiting'",
                                      (approval["id"],)):
            if V._loads(row["params_json"], {}).get("regenerate"):
                continue
            other = store.db.query_one(
                f"SELECT id FROM aiv_gen_jobs WHERE project_id = ? AND target_type = ? AND target_id = ? AND "
                f"ref_kind = ? AND slot = ? AND COALESCE(approval_id, 0) != ? AND status IN ({marks}) LIMIT 1",
                (row["project_id"], row["target_type"], row["target_id"], row["ref_kind"], row["slot"],
                 approval["id"], "held", *ACTIVE))
            if other and _cas(store, int(row["id"]), ("awaiting",), status="cancelled",
                              error=f"дубль: эту цель уже делает запуск #{other['id']} — второй раз не плачу"):
                dupes += 1
        cur = store.db.execute("UPDATE aiv_gen_jobs SET status = 'approved', updated_at = CURRENT_TIMESTAMP "
                               "WHERE approval_id = ? AND status = 'awaiting'", (approval["id"],))
        kick()
        return {"aiv": {"approved_jobs": int(cur.rowcount or 0), "duplicate_jobs": dupes,
                        "note": "запуски одобрены — работник ИИ-видео их выполнит"
                                + (f"; {dupes} шт. уже делает другая карточка — не повторяю" if dupes else "")}}
    cur = store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = ?, updated_at = CURRENT_TIMESTAMP "
                           "WHERE approval_id = ? AND status = 'awaiting'",
                           (f"владелец: {action}{(' — ' + comment) if comment else ''}"[:300], approval["id"]))
    return {"aiv": {"cancelled_jobs": int(cur.rowcount or 0)}}


A.register_card_action(CARD_ACTION, [("approve", "✅ Разрешить"), ("reject", "🗑 Отказ")], _on_decide)


def decide_card(store: Any, approval_id: int, action: str, comment: str = "", actor: str = "ui") -> dict:
    """Решение владельца по карточке этапа — идемпотентно: повтор того же решения (повтор запроса
    после ошибки клиента, двойной клик) ничего не запускает второй раз и не падает, а отдаёт
    текущее состояние. Противоположное решение по уже решённой карточке — ошибка, как раньше."""
    card = A.get_approval(store, approval_id)
    if (card.get("payload") or {}).get("action") != CARD_ACTION:
        raise V.AivError(f"карточка #{approval_id} — не этап ИИ-видео")
    want = A.DECISION_STATUS.get(action)
    if card["status"] == "pending":
        try:
            return A.decide(store, approval_id, action, comment=comment, actor=actor)
        except ValueError:
            card = A.get_approval(store, approval_id)  # решили параллельно — ниже: то же ли решение
            if card["status"] == "pending":
                raise
    if want and card["status"] == want:
        counts = {row["status"]: int(row["n"]) for row in store.db.query_all(
            "SELECT status, COUNT(*) AS n FROM aiv_gen_jobs WHERE approval_id = ? GROUP BY status", (card["id"],))}
        return {"ok": True, "status": card["status"], "already": True, "approval": card,
                "aiv": {"jobs": counts, "note": f"карточка #{approval_id} уже {card['status']} — повторно ничего не "
                                                "запускаю"}}
    raise ValueError(f"карточка #{approval_id} уже решена: {card['status']}")


# --------------------------------------------------------------------------- #
# Промпты и референсы
# --------------------------------------------------------------------------- #


def _style_text(style: dict) -> str:
    parts = [STYLE_BASE.get(style["style_kind"], STYLE_BASE["anime"])]
    if style.get("passport_text"):
        parts.append(f"Style passport (verbatim): {style['passport_text']}")
    if style.get("palette"):
        parts.append("Palette: " + ", ".join(style["palette"]))
    return ". ".join(parts)


def _asset_path(store: Any, ref: dict | None) -> Path | None:
    if not ref or not ref.get("asset_id"):
        return None
    try:
        path = Path(store.get_montage_asset(int(ref["asset_id"]))["file_path"])
    except KeyError:
        return None
    return path if path.is_file() else None


def _canon_refs(store: Any, project_id: int, character: dict) -> list[Path]:
    """Канон персонажа: картинки владельца (≤2) + выбранный разворот."""
    paths = []
    uploads = [r for r in V.list_refs(store, project_id, "character", character["id"], "upload")][-2:]
    for ref in uploads:
        p = _asset_path(store, ref)
        if p:
            paths.append(p)
    turn = V.selected_ref(store, project_id, "character", character["id"], "turnaround")
    p = _asset_path(store, turn)
    if p:
        paths.append(p)
    return paths


def video_last_frame(store: Any, ref: dict | None) -> Path | None:
    """Последний кадр видео-версии (кэш рядом с файлом видео). Конвейер v3 его НЕ использует (старт
    следующего плана — общий конечный кадр); оставлено для ручной сверки агентом."""
    from app.aivideo_providers import last_frame

    src = _asset_path(store, ref)
    if src is None:
        return None
    dest = src.with_name(f"{src.stem}.last.jpg")
    if not dest.is_file():
        try:
            last_frame(src, dest)
        except ProviderError:
            return None
    return dest


def _prev_frame(store: Any, project_id: int, prev: dict | None) -> tuple[Path | None, str]:
    """Последний кадр прошлого плана: его конечный кадр (у плана с видео), иначе его стартовый."""
    if not prev:
        return None, ""
    path = _asset_path(store, end_frame_ref(store, project_id, prev))
    if path:
        return path, "end frame"
    shots = V.list_shots(store, int(prev["script_id"]))
    n = next((i for i, s in enumerate(shots) if int(s["id"]) == int(prev["id"])), None)
    if n is None:
        return None, ""
    path = _asset_path(store, start_frame_ref(store, project_id, shots, n)[0])
    return (path, "keyframe") if path else (None, "")


def _style_paths(store: Any, project_id: int, limit: int = 1) -> list[Path]:
    paths = []
    for ref in V.list_refs(store, project_id, "style", 0):
        if ref["kind"] in ("style", "upload") and (ref["selected"] or ref["kind"] == "upload"):
            p = _asset_path(store, ref)
            if p:
                paths.append(p)
    return paths[:limit]


def _identity(character: dict) -> str:
    out = f"«{character['name']}» — identity card (verbatim, do not alter): {character['identity_card']}"
    if character.get("constraints"):
        out += f". Physical constraints (must always hold): {character['constraints']}"
    return out


PHYSICS = ("Physically plausible: every character and object rests on the floor or on furniture (nothing floats "
           "unless it is held), nothing is duplicated, no extra copies of any character or prop.")
LOCATION_PLATE_HINT = ("Reference {k} is the background plate of «{name}»: this exact room in every shot — same layout, "
                       "walls, floor, furniture and their positions, windows, lighting and colors; only the camera "
                       "angle and framing may differ, never redesign the room.")
LOCATION_VIDEO_HINT = "The background stays this exact location for the whole shot: the room never changes or morphs."


def shot_location(store: Any, project_id: int, shot: dict | None) -> dict | None:
    slug = (shot or {}).get("location") or ""
    return V.location_by_slug(store, project_id, slug) if slug else None


def location_text(loc: dict) -> str:
    """Описание локации — ОДНО и то же дословно во всех планах (кадры и видео): комната не «плывёт»."""
    return f"Location «{loc['name']}» (the same place in every shot set here, verbatim): {loc['description']}."


def location_plate(store: Any, project_id: int, loc: dict | None) -> Path | None:
    """Плита локации (выбранная версия) — общий референс фона всех её планов."""
    if not loc:
        return None
    return _asset_path(store, V.selected_ref(store, project_id, "location", int(loc["id"]), "location"))


def continuity_shot(shots: list[dict], n: int) -> tuple[dict | None, bool]:
    """План, чей кадр — референс непрерывности для старта ``shots[n]``: прошлый, если он в той же
    локации (``True`` — соседний); иначе последний раньше план этой же локации (``False`` — только
    комната); нет такого — (None, False). Кадр из другой локации не берём: он тянет чужой фон."""
    if n <= 0:
        return None, False
    here = shots[n].get("location") or ""
    prev = shots[n - 1]
    if (prev.get("location") or "") == here:
        return prev, True
    if not here:
        return None, False
    for k in range(n - 2, -1, -1):
        if (shots[k].get("location") or "") == here:
            return shots[k], False
    return None, False


def _frame_request(store: Any, job: dict, st: str, neg: str) -> tuple[str, list[Path], list[str], dict]:
    """Ключевой кадр плана: старт (``keyframe``) или конец (``endframe``). Референсы по важности
    (лимит модели — режем с конца): блокинг этого момента (композиция, позы, места, ракурс) → для
    конца — старт этого же плана, для старта — последний кадр прошлого плана → ВСЕ листы персонажей
    (разворот + эмоции) и облик → плита локации → картинки владельца → стиль."""
    from app import aivideo_blocking as B

    project_id = int(job["project_id"])
    which = "end" if job["ref_kind"] == "endframe" else "start"
    shot = V.get_shot(store, int(job["target_id"]))
    shots = V.list_shots(store, int(shot["script_id"]))
    n = next(i for i, s in enumerate(shots) if int(s["id"]) == int(shot["id"]))
    prev = shots[n - 1] if n else None
    nxt = shots[n + 1] if n + 1 < len(shots) else None
    cam = shot["camera"]
    missing: list[str] = []
    ranked: list[tuple[int, Path, str, str]] = []  # (важность, файл, роль для журнала, роль для модели)
    word = "LAST" if which == "end" else "FIRST"
    parts = [f"{st}. Vertical 9:16 film still — the {word} frame of shot {shot['idx']} (one continuous shot)."]
    block = _asset_path(store, V.selected_ref(store, project_id, "shot", int(shot["id"]), f"blocking_{which}"))
    blocking_info: dict[str, Any] = {"used": False}
    if block is not None:
        # Самоконтроль кадра макета: персонаж не в кадре/закрыт, вещь перед камерой на полкадра —
        # такой макет модель скопирует (оранжевая «дверь» в кадре); тогда кадр рисуется без него.
        check = B.frame_check(store, shot, which)
        if not check.get("ok"):
            reason = "; ".join(check.get("problems") or ["не прошёл самоконтроль"])[:300]
            blocking_info = {"used": False, "rejected": reason}
            missing.append(f"блокинг {which} отклонён самоконтролем: {reason}")
            block = None
    if block is not None:
        blocking_info = {"used": True}
        ranked.append((-3, block, f"блокинг: {'конец' if which == 'end' else 'старт'}",
                       "3D blocking mock-up of this exact moment (positions and camera only)"))
        parts.append("The 3D BLOCKING mock-up shows this exact moment. Use it ONLY for the camera angle and framing, "
                     "where each character is, their pose and the direction they face, and where furniture and props "
                     "stand. Do NOT copy its look: its simple shapes, plain flat colors and materials are placeholders "
                     "— draw the room exactly like the background plate and each character exactly from their sheets; "
                     "never draw mannequins, plain boxes, columns or the mock-up's colors. Its stand-ins: "
                     f"{B.legend(store, project_id, shot) or 'no characters'}.")
    elif not blocking_info.get("rejected"):
        missing.append(f"блокинг {which} (этап blocking, 0 ₽)")
    if which == "end":
        start, shared = start_frame_ref(store, project_id, shots, n)
        start_path = _asset_path(store, start)
        if start_path is not None:
            ranked.append((-2, start_path, f"план {shot['idx']}: старт" + (f" (конец плана {shared})" if shared else ""),
                           f"START frame of this same shot {shot['idx']}"))
            parts.append("The START frame of this same shot is given: keep exactly the same camera, background, "
                         "lighting, character designs, clothing and props; only what the action changes may differ.")
        else:
            missing.append(f"план {shot['idx']}: стартовый кадр")
        state = V.end_state_of(shot, nxt)
        if shot.get("action"):
            parts.append(f"This frame shows the RESULT of the single action «{shot['action']}» — the moment it ends.")
        text = V.state_text(store, project_id, state)
        if text:
            parts.append(f"Continuity state at the END of this shot (must match exactly): {text}.")
    else:
        # Непрерывность — только кадром ТОЙ ЖЕ сцены (той же локации): кадр из другой комнаты тянет
        # чужой фон. Прошлый план в другой локации — ищем последний план этой же локации раньше.
        cont, adjacent = continuity_shot(shots, n)
        prev_frame, prev_kind = _prev_frame(store, project_id, cont)
        if prev_frame is not None and adjacent:
            what = "конечный кадр" if prev_kind == "end frame" else "кадр"
            ranked.append((-1, prev_frame, f"план {cont['idx']}: {what}",
                           f"{prev_kind} of the previous shot {cont['idx']} (continuity)"))
            parts.append(f"A reference image is the {prev_kind} of the previous shot {cont['idx']}: keep the same "
                         "characters, faces, clothing, props, room, lighting and color continuous with it"
                         + (" — this shot continues the same scene." if shot.get("continues_from_prev") else
                            " (new angle or moment, same scene and the same room)."))
        elif prev_frame is not None:
            what = "конечный кадр" if prev_kind == "end frame" else "кадр"
            ranked.append((-1, prev_frame, f"план {cont['idx']}: {what} (та же локация)",
                           f"{prev_kind} of the earlier shot {cont['idx']} in this same location (room continuity)"))
            parts.append(f"A reference image is the {prev_kind} of the earlier shot {cont['idx']} in this same "
                         "location: the room must be exactly the same — layout, walls, furniture, windows, lighting "
                         "and colors; characters follow this shot's own state.")
        state = shot.get("state") or {}
        text = V.state_text(store, project_id, state)
        if text:
            parts.append(f"Continuity state at the start of this shot (must match exactly): {text}.")
        if shot.get("action"):
            parts.append(f"Action: {shot['action']}.")
    framing = ", ".join(x for x in (str(cam.get("shot") or "medium").replace("_", " ") + " shot",
                                    f"{cam['angle']} angle" if cam.get("angle") else "",
                                    f"{cam['lens']}mm lens" if cam.get("lens") else "") if x)
    parts.append(f"Camera: {framing}.")
    loc = shot_location(store, project_id, shot)
    caption = [f"план {shot['idx']}" + (": конец" if which == "end" else "")]
    plate = None
    if loc:
        # Одно и то же описание локации ДОСЛОВНО в каждом плане этой локации + её плита референсом
        # (сразу после кадров композиции и непрерывности — в лимит модели влезает всегда).
        parts.append(location_text(loc))
        plate = location_plate(store, project_id, loc)
        if plate:
            ranked.append((-0.5, plate, f"плита локации «{loc['name']}»", f"background plate of «{loc['name']}»"))
        else:
            missing.append(f"плита локации «{loc['name']}»")
    names = []
    for member in shot["cast"]:
        c = V.character_by_slug(store, project_id, member["id"])
        if not c:
            continue
        names.append(c["name"])
        text = _identity(c)
        look = next((l for l in c["looks"] if l["slug"] == member.get("look")), None)
        for sheet, label, role in (("turnaround", "разворот", "turnaround model sheet"),
                                   ("expressions", "эмоции", "expression sheet")):
            p = _asset_path(store, V.selected_ref(store, project_id, "character", c["id"], sheet))
            if p:
                ranked.append((0, p, f"{c['slug']}: {label}", f"{c['name']} — {role}"))
            else:
                missing.append(f"{c['slug']}: {label}")
        if look:
            text += f"; outfit/state: {look['outfit_text']}"
            lp = _asset_path(store, V.selected_ref(store, project_id, "look", look["id"], "look"))
            if lp:
                ranked.append((0, lp, f"{c['slug']}: облик {look['slug']}", f"{c['name']} — outfit «{look['name']}»"))
            else:
                missing.append(f"{c['slug']}: облик {look['slug']}")
        for up in [r for r in V.list_refs(store, project_id, "character", c["id"], "upload")][-2:]:
            p = _asset_path(store, up)
            if p:
                ranked.append((2, p, f"{c['slug']}: картинка владельца", f"{c['name']} — owner's reference"))
        if member.get("action"):
            text += f"; doing: {member['action']}"
        parts.append(text + f". Exactly one {c['name']} in the frame.")
    for p in _style_paths(store, project_id, 1):
        ranked.append((3, p, "стиль", "style reference"))
    ranked.sort(key=lambda r: r[0])  # устойчиво: порядок персонажей в кадре сохраняется
    seen: set[str] = set()
    chosen = []
    for item in ranked:
        if str(item[1]) not in seen:
            seen.add(str(item[1]))
            chosen.append(item)
    limit = max_refs(job["model"], job["provider"])
    dropped = [r[2] for r in chosen[limit:]]
    chosen = chosen[:limit]
    if chosen:
        parts.append("Reference images in order: " + "; ".join(f"{k} — {r[3]}" for k, r in enumerate(chosen, 1))
                     + ". Draw every character EXACTLY as in their sheets: same face, body, shape, colors and "
                       "details — not a different model or design.")
    if dropped:
        missing += [f"не влезло в лимит {limit}: {d}" for d in dropped]
    if plate is not None and any(r[1] == plate for r in chosen):
        k = next(i for i, r in enumerate(chosen, 1) if r[1] == plate)
        parts.append(f"{LOCATION_PLATE_HINT.format(k=k, name=loc['name'])}")
    props = V.prop_counts(state)
    if props:
        parts.append(f"Props: {props}.")
    emotions = ", ".join(d["emotion"] for d in shot["dialogue"] if d.get("emotion"))
    if emotions:
        parts.append(f"Emotion: {emotions}.")
    if shot["image_prompt"] and which == "start":
        parts.append(shot["image_prompt"])
    count = len(names)
    parts.append(f"Exactly {count} character{'s' if count != 1 else ''} in frame, no extra people. "
                 if count else "No people in frame. ")
    parts.append(PHYSICS + " " + NO_TEXT + neg)
    caption = [*caption, *(names or ["без персонажей"]), *([loc["name"]] if loc else [])]
    return " ".join(parts), [r[1] for r in chosen], caption, {"roles": [r[2] for r in chosen], "missing": missing,
                                                              "blocking": blocking_info}


PLATE_AVOID = ("people, person, human, girl, boy, woman, man, child, animal, pet, cat, dog, bird, robot, robot vacuum, "
               "vacuum cleaner robot, droid, mascot, character, creature, mannequin, doll, figure, silhouette, shadow of a "
               "person, reflection of a person, face")


def plate_prompt(store: Any, project_id: int, loc: dict, st: str, neg: str = "") -> str:
    """Плита локации — ПУСТАЯ комната: ни людей, ни животных, ни роботов, ни персонажей проекта и
    ничего на них похожего (иначе плита с пылесосом — и в кадрах вместо Бипа серый пылесос)."""
    cast = []
    for c in V.list_characters(store, project_id):
        card = re.split(r"[;.]", str(c.get("identity_card") or ""))[0].strip()[:90]
        cast.append(f"{c['name']} ({card})" if card else c["name"])
    who = (f" None of the series characters is here — not {', not '.join(cast)} — and nothing that looks like "
           "any of them." if cast else "")
    extra = f" Avoid: {PLATE_AVOID}" + (f", {neg.strip()[len('Avoid: '):].rstrip('.')}" if neg.strip().startswith(
        "Avoid: ") else "") + "."
    return (f"{st}. Empty background plate of the location: the empty room only, vertical 9:16 composition. NO people, "
            f"NO animals, NO robots, NO characters of any kind, no mannequins or figures — furniture and objects "
            f"only.{who} If the description names a character's thing (a charging dock, a pet bed, a bowl), draw "
            f"only that thing, empty — its owner is not in the room. Location «{loc['name']}»: {loc['description']}. "
            f"Consistent with the style reference. {NO_TEXT}{extra}")


def build_request(store: Any, job: dict) -> tuple[str, list[Path], list[str], dict]:
    """(промпт, файлы-референсы, подпись заглушки, {roles — что за референс по порядку, missing —
    чего не хватило}) запуска. Карта личности и физические ограничения — дословно."""
    ext = _ext_of_kind(job["ref_kind"])
    if ext is not None:
        return ext.build(store, job)
    project_id = int(job["project_id"])
    style = V.get_style(store, project_id)
    st = _style_text(style)
    neg = f" Avoid: {style['negative_text']}." if style.get("negative_text") else ""
    kind, target_id = job["ref_kind"], int(job["target_id"])
    if kind in FRAME_KINDS:
        return _frame_request(store, job, st, neg)
    refs: list[Path] = []
    roles: list[str] | None = None
    missing: list[str] = []
    caption = [REF_LABELS.get(kind, kind)]
    if kind == "style":
        project = P.get_project(store, project_id, include_sources=False)
        about = project.get("description") or project["name"]
        prompt = (f"{st}. Mood board image {job['slot'] or 1} of {STYLE_REF_COUNT} for an animated short series "
                  f"«{project['name']}»: {about}. Show the world, light and color mood. {NO_TEXT}{neg}")
        refs = [p for p in (_asset_path(store, r) for r in V.list_refs(store, project_id, "style", 0, "upload")) if p]
        caption += [project["name"], f"настроение {job['slot']}"]
    elif kind == "location":
        loc = V.get_location(store, target_id)
        prompt = plate_prompt(store, project_id, loc, st, neg)
        refs = _style_paths(store, project_id, 2)
        refs += [p for p in (_asset_path(store, r) for r in V.list_refs(store, project_id, "location", target_id,
                                                                         "upload")) if p]
        caption += [loc["name"]]
    elif kind in ("turnaround", "expressions"):
        c = V.get_character(store, target_id)
        uploads = [p for p in (_asset_path(store, r) for r in
                               V.list_refs(store, project_id, "character", target_id, "upload")) if p][-3:]
        if kind == "turnaround":
            prompt = (f"{st}. Character turnaround model sheet on a neutral light-grey background: front, three-quarter, "
                      f"side profile and back views, full body, the same character in every view, consistent "
                      f"proportions and colors. {_identity(c)}. {NO_TEXT}{neg}")
            refs = uploads + _style_paths(store, project_id, 1)
        else:
            prompt = (f"{st}. Expression sheet of the same character: six head-and-shoulders portraits in a 3x2 grid — "
                      f"neutral, happy, sad, angry, surprised, tired. {_identity(c)}. Keep the face, hair and colors "
                      f"exactly as in the reference sheet. {NO_TEXT}{neg}")
            refs = _canon_refs(store, project_id, c)
        caption += [c["name"]]
    elif kind == "look":
        # Облик серии — img2img-правка БАЗОВОГО листа (разворот первым референсом): та же раскладка
        # листа, то же лицо и тело, меняется только одежда/состояние.
        look = V.get_look(store, target_id)
        c = V.get_character(store, look["character_id"])
        prompt = (f"{st}. img2img edit of the FIRST reference image (the character's base turnaround model sheet): "
                  f"the same sheet layout (front, three-quarter, side profile and back views, full body, neutral "
                  f"light-grey background) and the SAME character — identical face, hair, body proportions, colors "
                  f"and design. Change ONLY the outfit/state: {look['outfit_text']}. {_identity(c)}. {NO_TEXT}{neg}")
        canon = _canon_refs(store, project_id, c)
        turn = _asset_path(store, V.selected_ref(store, project_id, "character", c["id"], "turnaround"))
        refs = ([turn] if turn else []) + [p for p in canon if p != turn]  # разворот — первым (база правки)
        roles = [("база: разворот" if p == turn else "картинка владельца") for p in refs]
        caption += [c["name"], look["name"]]
    else:
        raise V.AivError(f"неизвестный вид картинки {kind}")
    seen: set[str] = set()
    uniq = []
    kept_roles = []
    for i, p in enumerate(refs):
        key = str(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
            kept_roles.append(roles[i] if roles else p.name)
    limit = max_refs(job["model"], job["provider"])
    return prompt, uniq[:limit], caption, {"roles": kept_roles[:limit], "missing": missing}


def _file_digest(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def input_hash(model: str, params: dict, prompt: str, refs: list[Path], variant: int = 0) -> str:
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(json.dumps({k: v for k, v in params.items() if not k.startswith("_")}, sort_keys=True).encode("utf-8"))
    h.update(prompt.encode("utf-8"))
    for p in refs:
        h.update(_file_digest(p).encode("ascii"))
    h.update(str(variant).encode("ascii"))
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Запуск. Статусы: approved → submitting → polling → done | failed | blocked_policy | held |
# uncertain (ответ не дошёл — могло быть оплачено; сам не повторяю, сверка баланса).
# --------------------------------------------------------------------------- #


def get_job(store: Any, job_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_gen_jobs WHERE id = ?", (int(job_id),))
    if not row:
        raise KeyError(f"нет запуска {job_id}")
    data = dict(row)
    data["params"] = json.loads(data.pop("params_json") or "{}")
    return data


def _set_job(store: Any, job_id: int, **fields: Any) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    store.db.execute(f"UPDATE aiv_gen_jobs SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*fields.values(), int(job_id)))


def _cas(store: Any, job_id: int, expect: tuple[str, ...], where: str = "", where_args: tuple = (),
         **fields: Any) -> bool:
    """Сменить поля запуска, только если его статус — один из ``expect`` (сравнить-и-записать одним
    UPDATE). False — запуск уже у другого исполнителя/в другом состоянии: ничего не записано."""
    cols = ", ".join(f"{k} = ?" for k in fields)
    marks = ", ".join("?" for _ in expect)
    cur = store.db.execute(
        f"UPDATE aiv_gen_jobs SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status IN ({marks})"
        + (f" AND {where}" if where else ""), (*fields.values(), int(job_id), *expect, *where_args))
    return bool(cur.rowcount)


def _runner_id() -> str:
    return f"{threading.current_thread().name}@{os.getpid()}"


def _preflight(store: Any, job: dict) -> list[str]:
    """Можно ли платить за этот запуск прямо сейчас (под замком)."""
    if not _paid(job["provider"], job["kind"]):
        return []
    out = []
    if not settings.aivideo_enabled:
        out.append("платная генерация выключена (AIVIDEO_ENABLED=0)")
    if not job.get("approval_id"):
        return out + ["нет карточки одобрения — платный запуск без «да» владельца не идёт"]
    try:
        card = A.get_approval(store, int(job["approval_id"]))
    except KeyError:
        return out + [f"карточки #{job['approval_id']} нет"]
    if card["status"] != "approved":
        out.append(f"карточка #{card['id']} не одобрена ({card['status']})")
    amount = float((card.get("payload") or {}).get("amount_rub") or 0)
    # «Под вопросом» (ответ не дошёл, списание не подтверждено) — считаем потраченным по оценке.
    spent = spent_approval(store, card["id"]) + _sum(
        store, "SELECT COALESCE(SUM(estimate_rub), 0) FROM aiv_gen_jobs WHERE approval_id = ? AND status = 'uncertain' "
               "AND cost_source = '' AND id != ?", (card["id"], int(job["id"])))
    if spent + float(job["estimate_rub"]) > amount + 0.01:
        out.append(f"по карточке #{card['id']} потрачено {spent:.2f} ₽ + {job['estimate_rub']:.2f} ₽ > одобренных "
                   f"{amount:.2f} ₽ — нужна новая карточка")
    out += cap_problems(store, int(job["project_id"]), job.get("script_id"), float(job["estimate_rub"]), job["stage"],
                        reserved=False)
    return [p for p in out if "больше потолка одного этапа" not in p]


def _claim(store: Any, job_id: int) -> tuple[dict | None, list[str]]:
    """Взять запуск в работу: проверка денег и ``approved → submitting`` в ОДНОЙ записи-транзакции
    (BEGIN IMMEDIATE). Сколько бы исполнителей ни было (работник, aiv_run_approved, второй процесс),
    запуск отправляется один раз: кто не успел — получает ``None``."""
    with _LOCK, store.db._lock:
        conn = store.db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM aiv_gen_jobs WHERE id = ?", (int(job_id),)).fetchone()
            if not row or row["status"] != "approved":
                conn.rollback()
                return None, []
            problems = _preflight(store, get_job(store, job_id))
            if problems:
                conn.execute("UPDATE aiv_gen_jobs SET status = 'held', error = ?, updated_at = CURRENT_TIMESTAMP "
                             "WHERE id = ? AND status = 'approved'", ("; ".join(problems)[:500], int(job_id)))
                conn.commit()
                return None, problems
            cur = conn.execute(
                "UPDATE aiv_gen_jobs SET status = 'submitting', worker_id = ?, attempts = attempts + 1, error = '', "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'approved'", (_runner_id(), int(job_id)))
            conn.commit()
            if not cur.rowcount:
                return None, []
        finally:
            conn.close()
        _INFLIGHT.add(int(job_id))
    return get_job(store, job_id), []


def _send(store: Any, job: dict, send: Any) -> Any:
    """POST провайдеру. Сбой ДО отправки тела (соединение/TLS не встали) — повтор сам, до
    ``SUBMIT_TRIES`` раз с паузой, без новой карточки: запрос не ушёл, денег не взяли. Любой
    другой сбой — наверх (``run_jobs`` решает: «под вопросом» или отказ)."""
    for attempt in range(1, SUBMIT_TRIES + 1):
        try:
            return send()
        except Exception as exc:  # noqa: BLE001 - разбираем ниже
            if network_phase(exc) != "before" or attempt >= SUBMIT_TRIES:
                raise
            _set_job(store, job["id"], error=f"сеть до отправки ({type(exc).__name__}) — повтор "
                                             f"{attempt + 1}/{SUBMIT_TRIES}")
            time.sleep(RETRY_BACKOFF_SEC[min(attempt - 1, len(RETRY_BACKOFF_SEC) - 1)])
    raise AssertionError("unreachable")


def _balance(provider: Any) -> float | None:
    """Баланс провайдера (если умеет), ₽; не вышло — None (сверка — подсказка, не условие)."""
    fn = getattr(provider, "balance", None)
    if fn is None:
        return None
    try:
        value = fn()
    except Exception:  # noqa: BLE001
        return None
    return round(float(value), 2) if value is not None else None


def _mark_not_sent(store: Any, job: dict, exc: BaseException) -> None:
    params = {**get_job(store, job["id"])["params"], "net": "not_sent", "retry_free": True}
    _cas(store, job["id"], ("submitting",), where="remote_id = ''", status="failed",
         params_json=json.dumps(params, ensure_ascii=False),
         error=(f"сеть: запрос не ушёл ({SUBMIT_TRIES} попытки, {type(exc).__name__}: {str(exc)[:160]}) — не "
                "оплачено; «Повторить» — без новой карточки"))


def _mark_uncertain(store: Any, job: dict, exc: BaseException, provider: Any, before: float | None) -> None:
    """Ответ провайдера не дошёл (запрос мог дойти): НЕ повторяем сами. Баланс до/после: не
    изменился — повтор одним нажатием без новой карточки; списано — трата в журнал по разнице."""
    after = None
    if before is not None:
        time.sleep(BALANCE_SETTLE_SEC)
        after = _balance(provider)
    what = f"{type(exc).__name__}: {str(exc)[:160]}"
    check: dict[str, Any] = {"before": before, "after": after, "charged": None}
    fields: dict[str, Any] = {}
    if before is not None and after is not None:
        charged = round(before - after, 2)
        check["charged"] = max(0.0, charged)
        if charged <= 0.009:
            retry_free = True
            error = (f"ответ провайдера не дошёл ({what}); баланс не изменился ({before:.2f} ₽) — не списано. "
                     "«Повторить» — без новой карточки")
        else:
            retry_free = False
            fields = {"cost_rub": charged, "cost_source": "balance"}
            error = (f"ответ провайдера не дошёл ({what}); по балансу списано ≈{charged:.2f} ₽, результата нет — "
                     "повтор только новой карточкой (aiv_regenerate)")
    else:
        retry_free = False
        error = (f"ответ провайдера не дошёл ({what}) — могло быть оплачено, баланс не узнать: проверь баланс "
                 "Polza; сам не повторяю (повтор — aiv_regenerate)")
    params = {**get_job(store, job["id"])["params"], "net": "uncertain", "balance_check": check,
              "retry_free": retry_free}
    if _cas(store, job["id"], ("submitting",), where="remote_id = ''", status="uncertain", error=error[:500],
            params_json=json.dumps(params, ensure_ascii=False), **fields) and fields:
        add_ledger(store, job, fields["cost_rub"], "balance", "ответ не дошёл — списание по разнице баланса")


def retry_job(store: Any, job_id: int, run: bool = False) -> dict:
    """«Повторить» без новой карточки — только запуск, который ТОЧНО не оплачен: запрос не ушёл
    (сеть до отправки) или ответ не дошёл, а баланс не изменился. Иначе — aiv_regenerate."""
    job = get_job(store, job_id)
    if job["status"] not in ("failed", "uncertain") or job["remote_id"]:
        raise V.AivError(f"запуск #{job_id} — {job['status']}: повторять нечего")
    if not job["params"].get("retry_free"):
        raise BudgetError(f"запуск #{job_id}: не подтверждено, что он не оплачен — повтор только новой карточкой "
                          "(aiv_regenerate)")
    params = {**job["params"], "retry_free": False, "retries": int(job["params"].get("retries") or 0) + 1}
    if not _cas(store, job_id, ("failed", "uncertain"), where="remote_id = ''", status="approved", error="",
                params_json=json.dumps(params, ensure_ascii=False)):
        raise V.AivError(f"запуск #{job_id} уже в другом состоянии")
    if run:
        return {"ok": True, "status": "ran", **run_jobs(store, [int(job_id)])}
    kick()
    return {"ok": True, "status": "approved", "job_id": int(job_id), "note": "повтор передан работнику (та же карточка)"}


def retry_card(store: Any, approval_id: int, run: bool = False) -> dict:
    """«Повторить» все точно не оплаченные запуски карточки (сеть) — без новой карточки."""
    rows = store.db.query_all("SELECT id FROM aiv_gen_jobs WHERE approval_id = ? AND status IN ('failed', 'uncertain') "
                              "AND remote_id = '' ORDER BY id", (int(approval_id),))
    ids = [int(r["id"]) for r in rows if get_job(store, int(r["id"]))["params"].get("retry_free")]
    for job_id in ids:
        retry_job(store, job_id)
    out: dict[str, Any] = {"ok": True, "retried": ids}
    if run and ids:
        out.update(run_jobs(store, ids))
    elif not ids:
        out["note"] = "повторять без новой карточки нечего (не подтверждено, что не оплачено — aiv_regenerate)"
    return out


def _save_asset(store: Any, job: dict, path: Path, kind: str, mime: str, label: str) -> dict:
    from app.montage_assets import assets_dir, probe_asset

    dest = assets_dir() / f"{uuid4().hex}-aiv-{job['ref_kind']}{path.suffix or '.jpg'}"
    shutil.move(str(path), dest)
    meta = probe_asset(dest)
    if kind == "audio":
        meta = {**meta, "width": 0, "height": 0}
    return store.create_montage_asset(
        kind=kind, label=label[:120], description=f"ИИ-видео: {job['label']} ({job['provider']}/{job['model']})"[:500],
        tags="ии-видео", file_path=str(dest), original_filename=dest.name, mime_type=mime,
        size_bytes=dest.stat().st_size, **meta)


def _done(store: Any, job: dict, asset_id: int, prompt: str, cost: float, source: str, note: str = "",
          own_asset: bool = True) -> dict | None:
    """Готово: версия + трата. Записывает только ПЕРВЫЙ «готово» (сравнить-и-записать статуса):
    второй опрос того же запуска (другой поток/процесс) не плодит версию и не пишет вторую трату."""
    if not _cas(store, job["id"], ("submitting", "polling"), status="done", cost_rub=round(cost, 4),
                cost_source=source, error=""):
        _log.warning("aiv job %s: повторное «готово» пропущено (уже %s)", job["id"], get_job(store, job["id"])["status"])
        if own_asset:
            try:
                store.delete_montage_asset(int(asset_id))
            except KeyError:
                pass
        return None
    if _paid(job["provider"], job["kind"]) and cost > 0:
        add_ledger(store, job, cost, source, note)
    ref = V.add_ref(store, int(job["project_id"]), job["target_type"], int(job["target_id"]), job["ref_kind"],
                    int(asset_id), prompt=prompt, job_id=int(job["id"]), slot=job.get("slot") or "")
    _set_job(store, job["id"], result_ref_id=ref["id"])
    if auto_check(store, job, ref) is not None:
        ref = V.get_ref(store, int(ref["id"]))
    return ref


def _run_tts(store: Any, job: dict) -> dict | None:
    from app.aivideo_providers import get_tts

    params = job["params"]
    text = str(params.get("text") or "")
    voice = params.get("voice") or ""
    style = params.get("style") or ""
    tts = get_tts(job["provider"], job["model"], voice, style or None)
    work = settings.tmp_dir / f"aiv-tts-{uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        raw = work / "line.mp3"
        speech = _send(store, job, lambda: tts.synthesize(text, raw))
        cost = speech.cost_rub
        source = "provider" if cost is not None else "estimate"
        cost = float(cost) if cost is not None else float(job["estimate_rub"])
        asset = _save_asset(store, job, Path(speech.path), "audio", "audio/mpeg", f"ИИ-видео: {job['label']}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    key = _line_key({"provider": job["provider"], "model": job["model"], "voice": voice, "style": style}, text)
    return _done(store, job, asset["id"], key, cost, source)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _cached(store: Any, digest: str) -> Any:
    return store.db.query_one(
        "SELECT j.id, r.asset_id FROM aiv_gen_jobs j JOIN aiv_refs r ON r.id = j.result_ref_id "
        "WHERE j.input_hash = ? AND j.status = 'done' AND r.asset_id IS NOT NULL ORDER BY j.id DESC LIMIT 1", (digest,))


def _variant(store: Any, job: dict) -> int:
    if not job["params"].get("regenerate"):
        return 0
    return len(V.list_refs(store, job["project_id"], job["target_type"], job["target_id"], job["ref_kind"],
                           job.get("slot") or "")) + 1


def _run_sfx(store: Any, job: dict) -> dict | None:
    """Реплика персонажа-«пищалки»: запись пака (в «Файлы для монтажа» — тот же файл переиспользуется)."""
    from app.motion_assets import _parse_ref, import_to_montage_library, resolve_pack_ref

    ref = str(job["params"].get("voice") or SFX_DEFAULT)
    path = resolve_pack_ref(ref)
    if path is None:
        raise V.AivError(f"звука {ref} нет в паке (пак freesound не установлен? tools/fetch_sfx.py) — "
                         "синтетику не подставляю")
    _kind, pack_id, _file = _parse_ref(ref)
    asset = import_to_montage_library(store, pack_id, path.name)
    key = _line_key({"provider": "none", "model": "sfx", "voice": ref}, str(job["params"].get("text") or ""))
    return _done(store, job, int(asset["id"]), key, 0.0, "free", own_asset=False)


# --------------------------------------------------------------------------- #
# Отправка: 1.1 (сеть до отправки — повтор, после — «под вопросом» со сверкой баланса) +
# восстановление запроса у провайдера, если адаптер это умеет (Polza: 504 → /history)
# --------------------------------------------------------------------------- #


def _can_recover(provider: Any) -> bool:
    return callable(getattr(provider, "recover", None))


def _write_remote(store: Any, job: dict, remote: Any, expect: tuple[str, ...] = ("submitting", "failed")) -> bool:
    """remote_id пишется один раз (remote_id = ''), даже если запуск успели счесть «потерянным»."""
    marks = ", ".join("?" for _ in expect)
    cur = store.db.execute(
        "UPDATE aiv_gen_jobs SET status = 'polling', remote_id = ?, updated_at = CURRENT_TIMESTAMP "
        f"WHERE id = ? AND remote_id = '' AND status IN ({marks})", (str(remote), int(job["id"]), *expect))
    if not cur.rowcount:
        _log.warning("aiv job %s: remote_id %s не записан — запуск уже в другом состоянии", job["id"], remote)
        return False
    return True


def _submit(store: Any, job: dict, provider: Any, full: dict, refs: list[Path],
            poll_timeout: float | None) -> dict | None:
    """POST провайдеру. Время отправки пишется ДО запроса. Сбой до отправки тела — повтор сам
    (``_send``, 1.1). Запрос ушёл, ответа нет (``SubmitUncertain``: 504/обрыв чтения) и адаптер
    умеет искать (``recover``) — запуск ``recovering``, POST не повторяется никогда. Иначе сбой
    наверх: ``run_jobs`` ставит «под вопросом» со сверкой баланса (1.1) или отказ."""
    _set_job(store, job["id"], submitted_at=_utc_now())
    try:
        remote = _send(store, job, lambda: provider.submit(full, refs))
    except Exception as exc:  # noqa: BLE001 - разбор ниже / в run_jobs
        if _can_recover(provider) and (isinstance(exc, SubmitUncertain) or network_phase(exc) == "after"):
            if not _cas(store, job["id"], ("submitting",), where="remote_id = ''", status="recovering",
                        error=f"{str(exc)[:300]} — ищу запрос у провайдера, повторно не отправляю"[:500]):
                return None
            return _recover(store, get_job(store, job["id"]), provider, poll_timeout, RECOVER_ATTEMPTS)
        raise
    # ДО опроса: после перезапуска опрос продолжится, повторного POST (и оплаты) не будет.
    if not _write_remote(store, job, remote):
        return None
    return _finish(store, get_job(store, job["id"]), provider, poll_timeout)


def _claimed_remote_ids(store: Any, job_id: int) -> set[str]:
    rows = store.db.query_all("SELECT remote_id FROM aiv_gen_jobs WHERE remote_id != '' AND id != ?", (int(job_id),))
    return {str(r["remote_id"]) for r in rows}


def _recover(store: Any, job: dict, provider: Any, poll_timeout: float | None, attempts: int = 1) -> dict | None:
    """Найти у провайдера запрос, ушедший без ответа. Нашли — опрос как обычно; не нашли за срок
    адаптера — «под вопросом» (uncertain, 1.1): повтор только новой карточкой; пока ищем —
    ``recovering`` (работник спросит ещё)."""
    from datetime import datetime, timezone

    from app.aivideo_providers import parse_utc

    if not _can_recover(provider):
        _cas(store, job["id"], ("recovering",), status="uncertain",
             error="запрос ушёл без ответа, провайдер не умеет найти его — могло быть оплачено; проверь баланс, "
                   "повтор только новой карточкой (aiv_regenerate)")
        return None
    for attempt in range(max(1, attempts)):
        found = None
        try:
            found = provider.recover(job["model"], job.get("submitted_at") or "",
                                     _claimed_remote_ids(store, job["id"]))
        except Exception as exc:  # noqa: BLE001 - сеть: спросим следующим проходом
            _log.warning("aiv recover %s: %s", job["id"], exc)
        if found and found.get("id"):
            with _LOCK:
                ok = str(found["id"]) not in _claimed_remote_ids(store, job["id"]) and _cas(
                    store, job["id"], ("recovering",), where="remote_id = ''", status="polling",
                    remote_id=str(found["id"]),
                    error=f"id {found['id']} найден у провайдера после обрыва ответа — POST не повторял")
            if ok:
                return _finish(store, get_job(store, job["id"]), provider, poll_timeout)
        if attempt + 1 < attempts and RECOVER_INTERVAL_SEC > 0:
            time.sleep(RECOVER_INTERVAL_SEC)
    sent = parse_utc(job.get("submitted_at"))
    deadline = float(getattr(provider, "recover_deadline_sec", RECOVER_DEADLINE_SEC))
    if sent is not None and (datetime.now(timezone.utc) - sent).total_seconds() > deadline:
        params = {**get_job(store, job["id"])["params"], "net": "uncertain", "retry_free": False}
        _cas(store, job["id"], ("recovering",), where="remote_id = ''", status="uncertain",
             params_json=json.dumps(params, ensure_ascii=False),
             error=f"за {int(deadline // 60)} мин запрос у провайдера не появился — скорее всего не списано "
                   "(проверь баланс); повтор только новой карточкой (aiv_regenerate)")
    return None


def _run_image(store: Any, job: dict, provider: Any, poll_timeout: float | None) -> dict | None:
    prompt, refs, caption, ref_info = build_request(store, job)
    params = model_params(job["model"], prompt, job["params"].get("aspect") or "9:16",
                          job["params"].get("resolution") or "1K", job["provider"])
    digest = input_hash(job["model"] + "|" + job["provider"], params, prompt, refs, _variant(store, job))
    cached = _cached(store, digest)
    _set_job(store, job["id"], prompt=prompt, input_hash=digest,
             params_json=json.dumps({**job["params"], "request": {k: v for k, v in params.items() if k != "prompt"},
                                     "refs": [p.name for p in refs], "ref_roles": ref_info["roles"],
                                     "missing_refs": ref_info["missing"],
                                     **({"blocking": ref_info["blocking"]} if ref_info.get("blocking") else {})},
                                    ensure_ascii=False))
    if cached:
        return _done(store, job, int(cached["asset_id"]), prompt, 0.0, "cache", f"тот же запрос, что #{cached['id']}",
                     own_asset=False)
    full = {**params, "_kind": job["ref_kind"], "_caption": caption}
    return _submit(store, job, provider, full, refs, poll_timeout)


# --------------------------------------------------------------------------- #
# Видео плана и сцепка
# --------------------------------------------------------------------------- #


def _neighbors(store: Any, shot: dict) -> tuple[dict | None, dict | None]:
    shots = V.list_shots(store, shot["script_id"])
    ids = [int(s["id"]) for s in shots]
    if int(shot["id"]) not in ids:
        return None, None
    i = ids.index(int(shot["id"]))
    return (shots[i - 1] if i else None), (shots[i + 1] if i + 1 < len(shots) else None)


def build_video_prompt(store: Any, job: dict, shot: dict | None = None, mode: str = "start") -> str:
    """Промпт видео: ОДИН непрерывный план без склеек, одно простое действие, спокойная камера,
    персонажи по картам личности с физическими ограничениями («ровно один», «на полу»), счёт
    реквизита, состояние на старте и на конце (в режиме ``first_last`` модель интерполирует к
    данному конечному кадру), звук без речи. Негатив — отдельно (``VIDEO_NEGATIVE``: полем или
    блоком «Avoid» в ``video_params``)."""
    shot = shot or V.get_shot(store, int(job["target_id"]))
    project_id = int(job["project_id"])
    style = V.get_style(store, project_id)
    _prev, nxt = _neighbors(store, shot)
    parts = [f"{STYLE_BASE.get(style['style_kind'], STYLE_BASE['anime'])} animation, vertical 9:16.",
             "Single continuous shot, one take, no cuts, no scene change, no transitions, no new characters."]
    if mode == "first_last":
        parts.append("The FIRST and the LAST frame are given: animate smoothly from the first frame to the last "
                     "frame; the final frame must match the given last frame exactly (same positions, poses, props).")
    else:
        parts.append("Start exactly from the given first frame: same characters, faces, clothing, props, lighting "
                     "and background.")
    if style.get("passport_text"):
        parts.append(f"Style: {style['passport_text']}.")
    loc = shot_location(store, project_id, shot)
    if loc:  # то же описание, что в промптах ключевых кадров этой локации — дословно
        parts.append(f"{location_text(loc)} {LOCATION_VIDEO_HINT}")
    move = (shot.get("camera") or {}).get("move") or "static"
    parts.append(f"Camera: {GENTLE_CAMERA.get(move, 'locked-off camera')}.")
    if shot.get("action"):
        parts.append(f"Exactly one simple action in this shot: {shot['action']}.")
    for member in shot.get("cast") or []:
        c = V.character_by_slug(store, project_id, member["id"])
        if not c:
            continue
        text = f"{c['name']}: {c['identity_card']}"
        if c.get("constraints"):
            text += f"; physical constraints (always): {c['constraints']}"
        if member.get("action"):
            text += f"; does: {member['action']}"
        parts.append(text + f"; exactly one {c['name']} in frame.")
    if shot.get("motion_prompt"):
        parts.append(f"Motion: {shot['motion_prompt']}.")
    start_state = V.state_text(store, project_id, shot.get("state") or {})
    if start_state:
        parts.append(f"At the start: {start_state}.")
    end = V.end_state_of(shot, nxt)
    end_state = V.state_text(store, project_id, end)
    if end_state:
        parts.append(f"At the end of the shot: {end_state}.")
    props = V.prop_counts(shot.get("state") or {}) or V.prop_counts(end)
    if props:
        parts.append(f"Props: {props} — the count never changes.")
    count = len(shot.get("cast") or [])
    parts.append((f"Exactly {count} character{'s' if count != 1 else ''} in frame. " if count else "")
                 + "Gravity and contact: characters and objects stay on the floor or furniture, nothing floats or "
                   "duplicates.")
    talkers = [d for d in shot.get("dialogue") or [] if speech_of(store, project_id, d["who"])[1] == "voice"]
    if talkers:
        emotions = ", ".join(d["emotion"] for d in talkers if d.get("emotion"))
        parts.append("The character talks" + (f" ({emotions})" if emotions else "") + ", natural mouth movement.")
    sound = shot.get("sound") or ""
    parts.append((f"Sound: {sound}. " if sound else "Sound: natural ambient room tone and effects of the action. ")
                 + "No music, no speech, no voice-over.")
    parts.append(NO_TEXT)
    return " ".join(parts)[:3600]


def video_negative(style: dict) -> str:
    extra = (style.get("negative_text") or "").strip().rstrip(".")
    return VIDEO_NEGATIVE + (f", {extra}" if extra else "")


def _run_video(store: Any, job: dict, provider: Any, poll_timeout: float | None) -> dict | None:
    """Видео плана из пары ключевых кадров: старт (свой или конечный кадр прошлого плана) и конец.
    Режим — по возможностям модели: ``first_last`` (``images=[старт, конец]``, без листов — они
    у Seedance/H3 несовместимы с кадрами), ``start`` (один старт) или ``refs`` (старт + листы — только
    у моделей, где это совместимо)."""
    shot = V.get_shot(store, int(job["target_id"]))
    project_id = int(job["project_id"])
    shots = V.list_shots(store, int(shot["script_id"]))
    n = next(i for i, s in enumerate(shots) if int(s["id"]) == int(shot["id"]))
    start_ref, shared = start_frame_ref(store, project_id, shots, n)
    tail = bool(shared) and tail_mode(store, project_id, shot.get("script_id"))
    if start_ref is None and tail:
        start_ref = make_tail_start(store, project_id, shots[n - 1], shot, job)
    start = _asset_path(store, start_ref)
    if start is None:
        _cas(store, job["id"], ("submitting",), status="failed",
             error=(f"нет старта: видео плана {shared} ещё нет — его последний кадр и есть старт" if tail else
                    "нет стартового кадра плана — этап keyframes" + (f" (конец плана {shared})" if shared else "")))
        return None
    end_ref = end_frame_ref(store, project_id, shot)
    end = _asset_path(store, end_ref)
    caps = video_capabilities(job["model"], job["provider"])
    ref_limit = int(caps.get("ref_images") or 0)
    # Плита локации — первым референсом (комната та же, что в кадрах), дальше листы персонажей; всё
    # в лимите модели. У Seedance/H3 кадры и референсы несовместимы — там фон держат сами кадры
    # (они нарисованы по этой же плите) и дословное описание локации в промпте.
    plate = location_plate(store, project_id, shot_location(store, project_id, shot)) if ref_limit else None
    sheets = ([plate] if plate else []) + [p for p in _cast_sheets(store, project_id, shot, ref_limit) if p != plate]
    sheets = sheets[:ref_limit]
    mode = choose_video_mode(job["model"], end is not None, bool(sheets), job["provider"])
    if caps.get("exclusive", True) and mode != "refs":
        sheets = []  # кадры и листы вместе — только у моделей, где режимы совместимы (Kling 4.0)
    frames = [start, end] if mode == "first_last" else [start]
    prompt = build_video_prompt(store, job, shot, mode)
    if sheets:
        prompt = (prompt + " " + (_PLATE_HINT + " " if plate in sheets else "") + _SHEETS_HINT)[:4000]
    style = V.get_style(store, project_id)
    seconds = int(job["params"].get("seconds") or video_seconds(shot, job["model"]))
    params = video_params(job["model"], prompt, seconds, job["params"].get("resolution") or "720p",
                          job["params"].get("aspect") or "9:16", job["provider"], negative=video_negative(style))
    digest = input_hash(job["model"] + "|" + job["provider"] + "|" + mode, params, prompt, [*frames, *sheets],
                        _variant(store, job))
    cached = _cached(store, digest)
    _set_job(store, job["id"], prompt=params["prompt"], input_hash=digest,
             params_json=json.dumps({**job["params"], "request": {k: v for k, v in params.items() if k != "prompt"},
                                     "mode": mode, "start_ref_id": start_ref["id"],
                                     "end_ref_id": end_ref["id"] if mode == "first_last" else None,
                                     "refs": [p.name for p in frames],
                                     "reference_sheets": [p.name for p in sheets]}, ensure_ascii=False))
    if cached:
        return _done(store, job, int(cached["asset_id"]), params["prompt"], 0.0, "cache",
                     f"тот же запрос, что #{cached['id']}", own_asset=False)
    full = {**params, "_kind": "shot_video", "_start": str(start), "_end": str(end) if mode == "first_last" else "",
            **({"_reference_images": [str(p) for p in sheets]} if sheets else {})}
    return _submit(store, job, provider, full, frames, poll_timeout)


GENTLE_CAMERA = {"static": "locked-off static camera", "push_in": "very slow subtle push-in",
                 "pull_out": "very slow subtle pull-out", "pan_left": "very slow gentle pan left",
                 "pan_right": "very slow gentle pan right", "tilt_up": "very slow gentle tilt up",
                 "tilt_down": "very slow gentle tilt down", "parallax_push": "very slow subtle push-in",
                 "orbit": "locked-off camera with a very slow subtle push-in", "float": "locked-off camera",
                 "dolly_zoom": "locked-off camera with a very slow subtle push-in",
                 "tilt_reveal": "very slow gentle tilt up"}
CAMERA_TEXT = GENTLE_CAMERA  # прежнее имя
_SHEETS_HINT = ("The characters must look exactly like in the reference character sheets (same design, shape, "
                "proportions and colours); never redesign them.")
_PLATE_HINT = ("The first reference image is the background plate of the location: keep exactly this room (layout, "
               "furniture, lighting, colours).")


def _video_takes_refs(model: str) -> bool:
    """Берёт ли модель листы персонажей ВМЕСТЕ с кадрами (не взаимоисключающие режимы)."""
    caps = video_capabilities(model)
    return bool(caps.get("ref_images")) and not caps.get("exclusive", True)


def _cast_sheets(store: Any, project_id: int, shot: dict, limit: int = 8) -> list[Path]:
    """Листы (разворот, эмоции) персонажей плана — для моделей, что берут их вместе с кадрами."""
    out: list[Path] = []
    if limit <= 0:
        return out
    for member in shot.get("cast") or []:
        c = V.character_by_slug(store, project_id, member["id"])
        if not c:
            continue
        for sheet in ("turnaround", "expressions"):
            p = _asset_path(store, V.selected_ref(store, project_id, "character", c["id"], sheet))
            if p and p not in out:
                out.append(p)
    return out[:limit]


WAIT = object()  # запуск ждёт, пока делается то, от чего он зависит (кадр-основа, разворот)


def _run_chain(store: Any, job: dict) -> Any:
    """Сцепка «последний кадр видео → старт» (v2) упразднена: старт сцепленного плана — общий
    конечный кадр прошлого плана (этап keyframes). Старые запуски из очереди — отмена без денег."""
    _cas(store, job["id"], ("submitting",), status="cancelled", cost_rub=0.0, cost_source="free",
         error="устарело: старт сцепленного плана — конечный кадр прошлого плана (этап keyframes), из видео "
               "ничего не вынимается")
    return None


def _frame_deps(store: Any, job: dict) -> list[tuple[int, str]]:
    """От чего зависит картинка (её делают в той же партии): облик/эмоции — от разворота персонажа;
    конечный кадр — от стартового кадра плана (или конечного кадра прошлого); стартовый — от
    последнего кадра прошлого плана."""
    kind = job.get("ref_kind")
    if job.get("kind") != "image":
        return []
    ext = _ext_of_kind(str(kind or ""))
    if ext is not None:
        # Расширение (истории: сцена ждёт листы актёров и плиту) — свои зависимости или никаких.
        return list(ext.deps(store, job)) if hasattr(ext, "deps") else []
    if kind in ("look", "expressions"):
        cid = int(job["target_id"]) if kind == "expressions" else int(V.get_look(store, int(job["target_id"]))[
            "character_id"])
        return [(cid, "character:turnaround")]
    if kind not in FRAME_KINDS:
        return []
    shot = V.get_shot(store, int(job["target_id"]))
    shots = V.list_shots(store, int(shot["script_id"]))
    n = next(i for i, s in enumerate(shots) if int(s["id"]) == int(shot["id"]))
    prev = shots[n - 1] if n else None
    if kind == "endframe":
        if _is_chain(shot, prev):
            return [(int(prev["id"]), "shot:endframe")]
        return [(int(shot["id"]), "shot:keyframe")]
    cont, _adjacent = continuity_shot(shots, n)  # кадр непрерывности — та же локация (см. _frame_request)
    if cont is None:
        return []
    deps = [(int(cont["id"]), "shot:endframe")] if cont.get("needs_motion") else []
    return deps + [(int(cont["id"]), "shot:keyframe")]


def _video_deps(store: Any, job: dict) -> list[tuple[int, str]]:
    """Видео сцепленного плана у модели без конечного кадра стартует с последнего кадра видео прошлого
    плана — ждёт его готовности."""
    if job.get("kind") != "video" or job.get("target_type") != "shot":
        return []
    shot = V.get_shot(store, int(job["target_id"]))
    prev, _nxt = _neighbors(store, shot)
    if not _is_chain(shot, prev):
        return []
    caps = video_capabilities(job.get("model") or "", job.get("provider"))
    if caps.get("first_last") and (job.get("params") or {}).get("start") != "video_tail":
        return []
    return [(int(prev["id"]), "shot:shot_video")]


def _deps_busy(store: Any, job: dict) -> bool:
    marks = ", ".join("?" for _ in ACTIVE)
    for target_id, what in _frame_deps(store, job) + _video_deps(store, job):
        target_type, ref_kind = what.split(":")
        kind = "video" if ref_kind == "shot_video" else "image"
        row = store.db.query_one(
            f"SELECT id FROM aiv_gen_jobs WHERE kind = ? AND target_type = ? AND target_id = ? AND ref_kind = ? "
            f"AND id != ? AND status IN ({marks}) LIMIT 1", (kind, target_type, int(target_id), ref_kind,
                                                             int(job["id"]), *ACTIVE))
        if row:
            return True
    return False


def _after_video(store: Any, job: dict) -> None:
    """Новое видео плана: если видеомодель не берёт конечный кадр, а следующий план его продолжает —
    последний кадр этого видео становится стартом следующего (``source=video_tail``), видео следующего
    устаревает. У моделей с первым+последним кадром ничего не сцепляется (старт — общий конечный кадр)."""
    if job.get("target_type") != "shot":
        return None
    shot = V.get_shot(store, int(job["target_id"]))
    _prev, nxt = _neighbors(store, shot)
    if not nxt or not _is_chain(nxt, shot):
        return None
    project_id = int(job["project_id"])
    if not tail_mode(store, project_id, shot.get("script_id")):
        return None
    try:
        make_tail_start(store, project_id, shot, nxt, job)
    except Exception as exc:  # noqa: BLE001 - видео готово; старт следующего вынется при его запуске
        _log.warning("aiv: хвост видео плана %s не вынут: %s", shot["idx"], exc)
    return None


def _after_frame(store: Any, job: dict) -> None:
    """Новый ключевой кадр → видео, которые на нём стоят, «устарели»: конечный кадр плана — его
    видео и видео сцепленного следующего (это его старт); стартовый — видео этого плана."""
    if job.get("ref_kind") not in FRAME_KINDS:
        return
    shot = V.get_shot(store, int(job["target_id"]))
    store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? AND kind = 'shot_video'",
                     (int(shot["id"]),))
    if job["ref_kind"] == "endframe":
        _prev, nxt = _neighbors(store, shot)
        if nxt and _is_chain(nxt, shot):
            store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? "
                             "AND kind = 'shot_video'", (int(nxt["id"]),))


def _run_blocking(store: Any, job: dict) -> dict | None:
    """Блокинг плана: раскладка (Gemini для всей серии / по state) → скриншот движком, 0 ₽."""
    from app import aivideo_blocking as B
    from app.motion_render import CompositorMissing

    shot = V.get_shot(store, int(job["target_id"]))
    try:  # комната локации: с плиты Gemini-зрением (один раз, кэш), иначе по описанию
        B.shot_layout(store, int(job["project_id"]), shot, vision=True)
    except Exception as exc:  # noqa: BLE001 - раскладка по описанию всё равно есть
        _log.warning("aiv: раскладка локации плана %s: %s", shot["idx"], exc)
    data = B.current(store, shot)
    if data.get("source") == "auto" and (not shot.get("blocking") or data.get("outdated")):
        only = None if not shot.get("blocking") else {int(shot["id"])}
        if only is None:  # раскладки ещё нет — пишем сразу всей серии (одна комната на локацию)
            missing = {int(s["id"]) for s in V.list_shots(store, int(shot["script_id"])) if not s.get("blocking")}
            only = missing or {int(shot["id"])}
        B.write_blocking(store, int(shot["script_id"]), only=only)
    which = str(job["ref_kind"]).replace("blocking_", "")
    try:
        ref = B.render(store, int(shot["id"]), which, job)
    except CompositorMissing as exc:
        _cas(store, job["id"], ("submitting",), status="failed", error=f"блокинг: {exc}"[:500])
        return None
    if _cas(store, job["id"], ("submitting",), status="done", cost_rub=0.0, cost_source="free", error="",
            result_ref_id=ref["id"]):
        return ref
    return None


def _finish(store: Any, job: dict, provider: Any, poll_timeout: float | None) -> dict | None:
    from app.aivideo_providers import poll

    with _LOCK:
        if job["id"] in _ACTIVE_JOBS:
            return None
        job = get_job(store, job["id"])  # свежий статус: уже готов/упал — второй раз не опрашиваем
        if job["status"] != "polling" or not job["remote_id"]:
            return None
        _ACTIVE_JOBS.add(job["id"])
    try:
        timeout = settings.aivideo_poll_timeout_sec if poll_timeout is None else poll_timeout
        try:
            result = poll(provider, job["remote_id"], timeout, interval=0.0 if provider.name == "none" else 3.0)
        except ProviderError as exc:
            _set_job(store, job["id"], error=f"опрос: {exc}"[:500])
            return None
        except Exception as exc:  # noqa: BLE001 - сеть: дочитаем позже, без нового POST
            _set_job(store, job["id"], error=f"опрос не удался, повторю: {exc}"[:500])
            return None
        if result.get("status") == "pending":
            if result.get("note"):
                _set_job(store, job["id"], error=f"{result['note']} — повторю позже (POST не повторяю)"[:500])
            return None
        cost = result.get("cost_rub")
        if result.get("status") == "failed":
            # Отказ провайдера (ошибка, модерация, «все провайдеры вернули ошибку»): за упавшую задачу
            # провайдер не списывает (kie, Polza) — в траты идёт только сумма, которую он САМ назвал.
            # Не назвал — 0 ₽: резерв освобождён (failed не в OPEN), серия не упирается в потолок.
            charged = round(max(0.0, float(cost)), 4) if cost is not None and provider.paid else 0.0
            error = str(result.get("error") or "отказ")[:440]
            error += f" — провайдер списал {charged:.2f} ₽" if charged > 0 else " — не списано (резерв освобождён)"
            if _cas(store, job["id"], ("polling",), status="blocked_policy" if result.get("policy") else "failed",
                    error=error[:500], cost_rub=charged, cost_source="provider" if cost is not None else "none")                     and charged > 0:
                add_ledger(store, job, charged, "provider", "отказ провайдера — списание по его ответу")
            return None
        video = job["kind"] == "video"
        tmp = settings.tmp_dir / f"aiv-{uuid4().hex}{'.mp4' if video else '.jpg'}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        if video:
            start = None
            if job["params"].get("start_ref_id"):
                try:
                    start = _asset_path(store, V.get_ref(store, int(job["params"]["start_ref_id"])))
                except KeyError:
                    start = None
            end = None
            if job["params"].get("end_ref_id"):
                try:
                    end = _asset_path(store, V.get_ref(store, int(job["params"]["end_ref_id"])))
                except KeyError:
                    end = None
            fetch_params = {**job["params"].get("request", {}), "_kind": "shot_video",
                            "_start": str(start) if start else "", "_end": str(end) if end else ""}
        else:
            fetch_params = {**job["params"].get("request", {}), "aspect_ratio": job["params"].get("aspect"),
                            "_kind": job["ref_kind"], "_caption": build_request(store, job)[2]}
        try:
            provider.fetch(result, tmp, fetch_params)
        except Exception as exc:  # noqa: BLE001 - ссылка может истечь: оплачено, но файла нет
            if network_phase(exc) is not None:
                # Сеть: результат у провайдера есть — следующий опрос возьмёт ссылку заново.
                _set_job(store, job["id"], error=f"скачивание: сеть ({type(exc).__name__}) — повторю позже"[:500])
                return None
            if _cas(store, job["id"], ("polling",), status="failed", error=f"результат не скачан: {exc}"[:500]) \
                    and provider.paid:
                add_ledger(store, job, float(cost) if cost is not None else float(job["estimate_rub"]),
                           "provider" if cost is not None else "estimate", "оплачено, файл не скачан")
            return None
        if video:
            asset = _save_asset(store, job, tmp, "video", "video/mp4", f"ИИ-видео: {job['label']}")
        else:
            mime = "image/png" if tmp.read_bytes()[:4] == b"\x89PNG" else "image/jpeg"
            if mime == "image/png":
                tmp = tmp.rename(tmp.with_suffix(".png"))
            asset = _save_asset(store, job, tmp, "image", mime, f"ИИ-видео: {job['label']}")
        source = "provider" if cost is not None else ("estimate" if provider.paid else "free")
        amount = float(cost) if cost is not None else (float(job["estimate_rub"]) if provider.paid else 0.0)
        ref = _done(store, job, asset["id"], job["prompt"], amount, source)
        if ref is not None:
            (_after_video if video else _after_frame)(store, job)
        return ref
    finally:
        with _LOCK:
            _ACTIVE_JOBS.discard(job["id"])


def _adapter(kind: str, provider: str) -> Any:
    """Адаптер провайдера для запуска: видео — видео-адаптер, остальное — картинки."""
    from app.aivideo_providers import get_image_provider, get_video_provider

    return (get_video_provider if kind == "video" else get_image_provider)(provider)


def _result_ref(store: Any, job_id: int) -> dict | None:
    rid = get_job(store, job_id).get("result_ref_id")
    try:
        return V.get_ref(store, int(rid)) if rid else None
    except KeyError:
        return None


def run_jobs(store: Any, job_ids: list[int], poll_timeout: float | None = None) -> dict:
    """Выполнить запуски по порядку. Упёрлись в потолок — остальные этой карточки ждут (held).
    Запуск, который уже взял другой исполнитель, пропускается (``skipped``) — не отправляется второй раз.
    Сцепка ждёт видео прошлого плана — она и остальные запуски её карточки остаются ``approved``
    (``waiting``) до следующего прохода работника (видео прошлого плана дочитает ``resume``)."""
    done, failed, held, pending, skipped, waiting = [], [], [], [], [], []
    providers: dict[tuple[str, str], Any] = {}
    stop_cards: set[int] = set()
    deferred: set[int] = set()
    for job_id in job_ids:
        job0 = get_job(store, job_id)
        card = int(job0.get("approval_id") or 0)
        if card in deferred:
            waiting.append(job_id)
            continue
        if job0.get("approval_id") in stop_cards:
            if _cas(store, job_id, ("approved",), status="held",
                    error="партия остановлена: упёрлись в потолок или ошибку"):
                held.append(job_id)
            continue
        job, problems = _claim(store, job_id)
        if job is None:
            if problems:
                held.append(job_id)
                if job0.get("approval_id"):
                    stop_cards.add(int(job0["approval_id"]))
            else:
                skipped.append(job_id)
            continue
        paid = _paid(job["provider"], job["kind"])
        stop = False
        provider = None
        if paid or job["kind"] in ("image", "video"):
            key = (job["kind"] if job["kind"] == "video" else "image", job["provider"])
            provider = providers.setdefault(key, _adapter(key[0], job["provider"]))
        before = _balance(provider) if paid else None
        try:
            if job["kind"] == "sfx":
                ref = _run_sfx(store, job)
            elif job["kind"] == "tts":
                ref = _run_tts(store, job)
            elif job["kind"] == "chain":
                ref = _run_chain(store, job)
            elif job["kind"] == "blocking":
                ref = _run_blocking(store, job)
            elif job["kind"] == "image" and _deps_busy(store, job):
                # Основа картинки (разворот, стартовый кадр, конец прошлого плана) ещё делается: ждём её,
                # иначе нарисуем без неё. Остальные запуски карточки — тоже до следующего прохода.
                _cas(store, job_id, ("submitting",), status="approved", worker_id="",
                     attempts=max(0, int(job["attempts"]) - 1), error="жду кадр-основу (делается)")
                deferred.add(card)
                waiting.append(job_id)
                continue
            elif job["kind"] == "video" and _deps_busy(store, job):
                # Старт — последний кадр видео прошлого плана, а оно ещё делается: ждём его (без POST).
                # Остальные видео карточки идут дальше — несвязанные не ждут.
                _cas(store, job_id, ("submitting",), status="approved", worker_id="",
                     attempts=max(0, int(job["attempts"]) - 1), error="жду видео прошлого плана (его последний "
                                                                      "кадр — мой старт)")
                waiting.append(job_id)
                continue
            elif job["kind"] == "video":
                ref = _run_video(store, job, provider, poll_timeout)
            else:
                ref = _run_image(store, job, provider, poll_timeout)
        except Exception as exc:  # noqa: BLE001 - один запуск не валит партию
            _log.warning("aiv job %s failed: %s", job_id, exc)
            phase = network_phase(exc)
            current = get_job(store, job_id)
            if current["remote_id"] or current["status"] == "recovering":
                pass  # есть remote_id / ищем у провайдера — не трогаем: дочитает resume (без нового POST)
            elif isinstance(exc, SubmitUncertain):
                # Ушло без ответа, а искать адаптер не умеет: 1.1 — «под вопросом» со сверкой баланса.
                if paid:
                    _mark_uncertain(store, job, exc, provider, before)
                else:
                    _cas(store, job_id, ("submitting",), status="failed", error=str(exc)[:500])
            elif isinstance(exc, ProviderError):
                _cas(store, job_id, ("submitting",), status="blocked_policy" if exc.policy else "failed",
                     error=str(exc)[:500])
                stop = exc.policy or exc.status in STOP_HTTP
            elif phase == "before":
                _mark_not_sent(store, job, exc)
            elif phase == "after" and paid:
                _mark_uncertain(store, job, exc, provider, before)
            else:
                hint = " (могло быть оплачено — проверь баланс; сам не повторяю)" if paid else ""
                _cas(store, job_id, ("submitting",), where="remote_id = ''",
                     error=f"{type(exc).__name__}: {str(exc)[:350]}{hint}", status="failed")
                stop = phase is None
            ref = None
        finally:
            with _LOCK:
                _INFLIGHT.discard(int(job_id))
        state = get_job(store, job_id)["status"]
        ref = ref or (_result_ref(store, job_id) if state == "done" else None)
        if ref is not None:
            done.append({"job_id": job_id, "ref_id": ref["id"], "label": job["label"], "url": ref["url"]})
        elif state in ("polling", "recovering"):
            pending.append(job_id)
        else:
            failed.append({"job_id": job_id, "label": job["label"], "status": state,
                           "error": get_job(store, job_id)["error"]})
            # Партию останавливают только деньги/политика/ключ и непонятные сбои; сеть и отказ
            # провайдера нарисовать один кадр — нет: остальные запуски идут дальше.
            if job.get("approval_id") and paid and (stop or state == "blocked_policy"):
                stop_cards.add(int(job["approval_id"]))
    return {"done": done, "failed": failed, "held": held, "polling": pending, "skipped": skipped,
            "waiting": waiting}


def run_pending(store: Any, approval_id: int | None = None, project_id: int | None = None,
                poll_timeout: float | None = None) -> dict:
    """Работник: дочитать опрос и поиск (без нового POST) и выполнить одобренные запуски."""
    resumed = resume(store, poll_timeout=poll_timeout)
    sql = "SELECT id FROM aiv_gen_jobs WHERE status = 'approved'"
    params: list[Any] = []
    if approval_id is not None:
        sql += " AND approval_id = ?"
        params.append(int(approval_id))
    if project_id is not None:
        sql += " AND project_id = ?"
        params.append(int(project_id))
    ids = [int(r["id"]) for r in store.db.query_all(sql + " ORDER BY id", params)]
    out = run_jobs(store, ids, poll_timeout) if ids else {"done": [], "failed": [], "held": [], "polling": [],
                                                          "skipped": [], "waiting": []}
    out["resumed"] = resumed
    return out


def run_approved(store: Any, approval_id: int, confirm: bool = False) -> dict:
    """Агент/REST: выполнить или продолжить запуски ОДОБРЕННОЙ владельцем карточки (обычно это делает
    работник сам сразу после «Разрешить»). Без ``confirm`` — только что будет запущено.

    Безопасно вызывать, пока работник занят той же карточкой: запуск берётся атомарно
    (``approved → submitting``), уже взятые работником только показываются (``in_progress``)."""
    card = A.get_approval(store, approval_id)
    if (card.get("payload") or {}).get("action") != CARD_ACTION:
        raise V.AivError(f"карточка #{approval_id} — не этап ИИ-видео")
    if card["status"] != "approved":
        raise BudgetError(f"карточка #{approval_id} не одобрена владельцем ({card['status']}) — запускать нечего")
    jobs = list_jobs(store, approval_id=approval_id)
    if not confirm:
        todo = [j for j in jobs if j["status"] in ("approved", "polling", "recovering", "held")]
        return {"ok": True, "status": "preview", "jobs": todo,
                "note": "confirm=true — запустить/продолжить (одобрено владельцем на "
                        f"{card['payload'].get('amount_rub')} ₽)"}
    store.db.execute("UPDATE aiv_gen_jobs SET status = 'approved', error = '', updated_at = CURRENT_TIMESTAMP "
                     "WHERE approval_id = ? AND status = 'held'", (int(approval_id),))
    ids = [int(r["id"]) for r in store.db.query_all(
        "SELECT id FROM aiv_gen_jobs WHERE approval_id = ? AND status = 'approved' ORDER BY id", (int(approval_id),))]
    out = run_jobs(store, ids)
    # Опрос/поиск этой карточки, брошенный после перезапуска (не идёт ни в одном потоке процесса) — дочитать.
    resumed = []
    with _LOCK:
        busy = set(_ACTIVE_JOBS) | set(_INFLIGHT)
    for row in store.db.query_all("SELECT id, kind, provider, status FROM aiv_gen_jobs WHERE approval_id = ? AND "
                                  "((status = 'polling' AND remote_id != '') OR status = 'recovering') ORDER BY id",
                                  (int(approval_id),)):
        if int(row["id"]) in busy or int(row["id"]) in out["polling"]:
            continue
        job = get_job(store, int(row["id"]))
        adapter = _adapter(row["kind"], row["provider"])
        ref = (_recover(store, job, adapter, None) if row["status"] == "recovering"
               else _finish(store, job, adapter, None))
        if ref is not None:
            resumed.append(int(row["id"]))
    progress = [{"job_id": j["id"], "label": j["label"], "status": j["status"], "worker_id": j.get("worker_id") or ""}
                for j in list_jobs(store, approval_id=approval_id)
                if j["status"] in ("submitting", "polling", "recovering")]
    note = "запуски карточки выполнены"
    if progress:
        note = (f"{len(progress)} шт. уже в работе (работник, опрос или поиск у провайдера) — повторно не отправляю; "
                "статус — aiv_job_status")
    return {"ok": True, "status": "ran", **out, "resumed": {"finished": resumed, "lost": []},
            "in_progress": progress, "note": note}


def resume(store: Any, poll_timeout: float | None = None) -> dict:
    """После перезапуска и каждым проходом работника:

    * ``polling`` — опрашиваем дальше (POST не повторяем);
    * ``recovering`` — ответа на POST не было: спрашиваем адаптер (``recover``), нашёлся ли запрос;
    * ``submitting`` без remote_id у мёртвого исполнителя (свой процесс и поток не в работе, без
      worker_id, или «отправляю» дольше ``SUBMIT_STALE_SEC``): время отправки записано и адаптер
      умеет искать → ``recovering``; сцепка (бесплатно, без POST) → снова ``approved``; иначе —
      НЕ повторяем сами (могли уже заплатить): ``failed`` «проверь баланс»."""
    lost, requeued, recovered, finished = [], [], [], []
    stale_where = "remote_id = '' AND (worker_id = '' OR worker_id LIKE ? OR updated_at < datetime('now', ?))"
    stale_args = (f"%@{os.getpid()}", f"-{int(SUBMIT_STALE_SEC)} seconds")
    for row in store.db.query_all("SELECT id FROM aiv_gen_jobs WHERE status = 'submitting' AND remote_id = ''"):
        with _LOCK:
            if int(row["id"]) in _ACTIVE_JOBS or int(row["id"]) in _INFLIGHT:
                continue  # отправляется прямо сейчас в другом потоке этого процесса
            job = get_job(store, int(row["id"]))
            if job["kind"] == "chain":
                if _cas(store, job["id"], ("submitting",), where=stale_where, where_args=stale_args,
                        status="approved", worker_id="", error="прервано — сцепка бесплатна, запущу снова"):
                    requeued.append(job["id"])
            elif (job["kind"] in ("image", "video") and job.get("submitted_at")
                  and _can_recover(_adapter(job["kind"], job["provider"]))):
                _cas(store, job["id"], ("submitting",), where=stale_where, where_args=stale_args,
                     status="recovering", error="прервано после отправки — ищу запрос у провайдера, повторно не "
                                                "отправляю")
            elif _cas(store, job["id"], ("submitting",), where=stale_where, where_args=stale_args, status="failed",
                      error="прервано при отправке — могло быть оплачено; проверь баланс, повтор только вручную "
                            "(перегенерация)"):
                lost.append(job["id"])
    for row in store.db.query_all("SELECT id FROM aiv_gen_jobs WHERE status = 'recovering' ORDER BY submitted_at, id"):
        with _LOCK:
            if int(row["id"]) in _ACTIVE_JOBS or int(row["id"]) in _INFLIGHT:
                continue
        job = get_job(store, int(row["id"]))
        ref = _recover(store, job, _adapter(job["kind"], job["provider"]), poll_timeout, attempts=1)
        state = get_job(store, job["id"])
        if state["remote_id"]:
            recovered.append(job["id"])
        if ref is not None:
            finished.append(job["id"])
    for row in store.db.query_all("SELECT id FROM aiv_gen_jobs WHERE status = 'polling' AND remote_id != '' ORDER BY id"):
        with _LOCK:
            if int(row["id"]) in _ACTIVE_JOBS or int(row["id"]) in _INFLIGHT:
                continue
        if int(row["id"]) in finished or int(row["id"]) in recovered:
            continue
        job = get_job(store, int(row["id"]))
        ref = _finish(store, job, _adapter(job["kind"], job["provider"]), poll_timeout)
        if ref is not None:
            finished.append(job["id"])
    return {"finished": finished, "lost": lost, "recovered": recovered, "requeued": requeued}


def list_jobs(store: Any, project_id: int | None = None, approval_id: int | None = None,
              script_id: int | None = None, limit: int = 200) -> list[dict]:
    where, params = [], []
    for col, value in (("project_id", project_id), ("approval_id", approval_id), ("script_id", script_id)):
        if value is not None:
            where.append(f"{col} = ?")
            params.append(int(value))
    sql = "SELECT * FROM aiv_gen_jobs" + (" WHERE " + " AND ".join(where) if where else "")
    rows = store.db.query_all(sql + " ORDER BY id DESC LIMIT ?", (*params, max(1, min(int(limit), 1000))))
    out = []
    for r in rows:
        data = dict(r)
        data.pop("params_json", None)
        data["prompt"] = (data.get("prompt") or "")[:400]
        out.append(data)
    return out


def job_counts(store: Any, project_id: int) -> dict:
    rows = store.db.query_all("SELECT status, COUNT(*) AS n FROM aiv_gen_jobs WHERE project_id = ? GROUP BY status",
                              (int(project_id),))
    return {r["status"]: int(r["n"]) for r in rows}


def cancel_jobs(store: Any, project_id: int, approval_id: int | None = None) -> dict:
    sql = ("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'отменено', updated_at = CURRENT_TIMESTAMP "
           "WHERE project_id = ? AND status IN ('awaiting', 'approved', 'held')")
    params: list[Any] = [int(project_id)]
    if approval_id is not None:
        sql += " AND approval_id = ?"
        params.append(int(approval_id))
    cur = store.db.execute(sql, params)
    return {"ok": True, "cancelled": int(cur.rowcount or 0)}


# --------------------------------------------------------------------------- #
# Проверка постоянства (Gemini-зрение, бесплатно; нет ключа — пропуск)
# --------------------------------------------------------------------------- #

REVIEW_MIN_SCORE = 6.0  # ниже — версия «проверь» (needs_review); перерисовка — только по решению владельца
AUTO_CHECK = True  # проверять кадр с персонажами сразу после генерации (Gemini, бесплатно)


def _vision_client() -> Any:
    from app.ai.gemini import GeminiClient

    return GeminiClient(timeout=90)


def auto_check(store: Any, job: dict, ref: dict) -> dict | None:
    """После платного кадра плана с персонажами — проверка постоянства (бесплатно). Не похоже
    (< 6) — версия ``needs_review``; ничего платного само не запускается. Gemini недоступен —
    отметка в оценке версии, запуск не падает."""
    if not AUTO_CHECK or job.get("ref_kind") not in (*FRAME_KINDS, "shot_video") or job.get("provider") == "none":
        return None
    if job["ref_kind"] == "shot_video":
        if not settings.gemini_api_key:
            return None
        try:  # видео — бесплатная проверка Gemini-зрением: счёт, дизайн, опора, склейки, конец
            return check_video(store, int(ref["id"]))
        except Exception as exc:  # noqa: BLE001 - проверка — подсказка, не условие
            _log.warning("aiv auto video check of ref %s failed: %s", ref["id"], exc)
            V.set_ref_score(store, int(ref["id"]), {"kind": "video_qa", "error": f"{type(exc).__name__}: "
                                                                                 f"{str(exc)[:200]}",
                                                    "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            return None
    if not V.get_shot(store, int(job["target_id"]))["cast"]:
        return None
    try:
        return check_consistency(store, int(ref["id"]))
    except Exception as exc:  # noqa: BLE001 - проверка — подсказка, не условие
        _log.warning("aiv auto consistency check of ref %s failed: %s", ref["id"], exc)
        V.set_ref_score(store, int(ref["id"]), {"error": f"{type(exc).__name__}: {str(exc)[:200]}",
                                                "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        return None


CONSISTENCY_PROMPT = """\
Ты проверяешь постоянство персонажей в мультсериале. Первая картинка — КАДР. Дальше по каждому
персонажу — его канон (лист разворота или картинка владельца) и карта личности текстом.
Для каждого персонажа ответь, тот же ли это персонаж в кадре: score 0–10 (10 — точно он: лицо,
волосы, цвета, пропорции, отличительная деталь), differences — что отличается, коротко по-русски.
Верни ТОЛЬКО JSON."""


def check_consistency(store: Any, ref_id: int, client: Any = None) -> dict:
    """Кадр против канона персонажей: оценка 0–10 и что отличается (пишется в версию)."""
    import base64

    ref = V.get_ref(store, ref_id)
    if ref["kind"] not in FRAME_KINDS:
        raise V.AivError("проверка постоянства — для кадра плана (kind keyframe / endframe)")
    if client is None and not settings.gemini_api_key:
        return {"ok": True, "skipped": True, "note": "Gemini не настроен — проверка пропущена (сравни сам глазами)"}
    client = client or _vision_client()
    shot = V.get_shot(store, ref["target_id"])
    frame = _asset_path(store, ref)
    if frame is None:
        raise V.AivError("файла кадра нет")

    def inline(path: Path) -> dict:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return {"inline_data": {"mime_type": mime, "data": base64.b64encode(path.read_bytes()).decode("ascii")}}

    parts: list[dict] = [{"text": CONSISTENCY_PROMPT}, {"text": "КАДР:"}, inline(frame)]
    cast = []
    for member in shot["cast"]:
        c = V.character_by_slug(store, ref["project_id"], member["id"])
        if not c:
            continue
        canon = _canon_refs(store, ref["project_id"], c)[-2:]
        sheet = _asset_path(store, V.selected_ref(store, ref["project_id"], "character", c["id"], "expressions"))
        if sheet:
            canon.append(sheet)
        if not canon:
            continue
        cast.append(c["slug"])
        parts.append({"text": f"Персонаж id={c['slug']} «{c['name']}». Карта: {c['identity_card']}. Канон:"})
        parts.extend(inline(p) for p in canon)
    if not cast:
        return {"ok": True, "skipped": True, "note": "в плане нет персонажей с каноном — сравнивать не с чем"}
    schema = {"type": "object", "properties": {"characters": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "string"}, "score": {"type": "number"},
                                         "differences": {"type": "string"}},
        "required": ["id", "score", "differences"]}}}, "required": ["characters"]}
    from app.ai.gemini import _extract_response_text

    payload = {"contents": [{"role": "user", "parts": parts}],
               "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
                                    "responseSchema": schema}}
    response = client.generate_content(settings.gemini_text_model, payload)
    try:
        data = json.loads(_extract_response_text(response))
    except json.JSONDecodeError as exc:
        raise V.AivError("Gemini вернул не-JSON") from exc
    chars = [{"id": str(c.get("id") or ""), "score": max(0.0, min(10.0, float(c.get("score") or 0))),
              "differences": P._text(c.get("differences"), 300)} for c in data.get("characters") or []
             if str(c.get("id") or "") in cast]  # оценки только тех, кто в кадре
    score = {"characters": chars, "min": min((c["score"] for c in chars), default=None),
             "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": settings.gemini_text_model}
    low = [c for c in chars if c["score"] < REVIEW_MIN_SCORE]
    V.set_ref_score(store, ref_id, score, needs_review=bool(low))
    return {"ok": True, "score": score, "needs_review": bool(low),
            "note": ("ниже 6 — кадр помечен «проверь» (needs_review); перерисовать — решение владельца "
                     "(aiv_regenerate, своя карточка): " + "; ".join(
                f"{c['id']}: {c['differences']}" for c in low)) if low else "персонажи похожи на канон"}


# --------------------------------------------------------------------------- #
# Проверка ГОТОВОГО видео плана (Gemini-зрение, бесплатно): 4–6 кадров против state и карт
# --------------------------------------------------------------------------- #

VIDEO_QA_FRAMES = 5
VIDEO_QA_MIN_SCORE = 6.0
VIDEO_QA_PROMPT = """\
Ты проверяешь сгенерированный видеоплан мультсериала перед монтажом. Даны кадры из видео по порядку
(с секундой), единственное действие плана, заявленное состояние на СТАРТЕ и на КОНЦЕ плана, персонажи
(карта личности, физические ограничения, канон-лист) и счёт реквизита.
Найди проблемы:
- count — лишние, пропавшие или раздвоившиеся персонажи/предметы (печенье стало двумя, второй робот);
- design — персонаж не похож на канон или переделан (робот-пылесос стал гуманоидом, другое лицо/цвета);
- floating — персонаж или предмет висит в воздухе без опоры (пылесос не на полу);
- cut — склейка, смена сцены или ракурса скачком внутри плана;
- action — действие нелогично, не то или их несколько;
- start_state / end_state — старт или конец не совпадают с заявленным состоянием;
- artifact — грубые артефакты (тянущиеся конечности, каша).
score 0–10 (10 — чисто), ok — можно в монтаж без перегенерации, issues [{what, detail — коротко
по-русски, at — секунда}]. Верни ТОЛЬКО JSON."""


def sample_video_frames(video: Path, dest_dir: Path, count: int = VIDEO_QA_FRAMES) -> list[tuple[float, Path]]:
    """4–6 кадров видео равномерно, первый и последний — у краёв (jpeg ≤ 512 px по высоте)."""
    import subprocess

    from app.dubbing import probe_duration

    count = max(4, min(6, int(count)))
    try:
        duration = float(probe_duration(video) or 0)
    except Exception:  # noqa: BLE001
        duration = 0.0
    duration = duration if duration > 0 else 4.0
    dest_dir.mkdir(parents=True, exist_ok=True)
    last = max(0.0, duration - 0.15)
    times = [round(0.05 + (last - 0.05) * k / (count - 1), 2) for k in range(count)]
    out = []
    for k, t in enumerate(times):
        path = dest_dir / f"qa-{k}.jpg"
        proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                               "-vf", "scale=-2:'min(512,ih)'", "-q:v", "4", str(path)],
                              capture_output=True, timeout=60, check=False)
        if proc.returncode == 0 and path.is_file() and path.stat().st_size:
            out.append((t, path))
    return out


def check_video(store: Any, ref_id: int, client: Any = None) -> dict:
    """Готовое видео плана против его start/end state, карт персонажей (дизайн, «ровно один», «на
    полу») и счёта реквизита. Плохо — версия ``needs_review`` (раскадровка и карточка этапа); платная
    перегенерация — только новой карточкой (одна кнопка, решает владелец)."""
    ref = V.get_ref(store, ref_id)
    if ref["kind"] != "shot_video":
        raise V.AivError("проверка видео — для видео плана (kind shot_video)")
    if client is None and not settings.gemini_api_key:
        return {"ok": True, "skipped": True, "note": "Gemini не настроен — проверка видео пропущена (посмотри сам)"}
    client = client or _vision_client()
    project_id = int(ref["project_id"])
    shot = V.get_shot(store, int(ref["target_id"]))
    _prev, nxt = _neighbors(store, shot)
    video = _asset_path(store, ref)
    if video is None:
        raise V.AivError("файла видео нет")
    work = settings.tmp_dir / f"aiv-qa-{uuid4().hex}"
    try:
        frames = sample_video_frames(video, work)
        if not frames:
            raise V.AivError("кадры из видео не вынулись (ffmpeg)")
        end = V.end_state_of(shot, nxt)
        parts: list[dict] = [{"text": VIDEO_QA_PROMPT}, {"text": (
            f"План {shot['idx']}. Действие: {shot.get('action') or '—'}. "
            f"Старт: {V.state_text(store, project_id, shot.get('state') or {}) or 'не задан'}. "
            f"Конец: {V.state_text(store, project_id, end) or 'не задан'}. "
            f"Реквизит: {V.prop_counts(shot.get('state') or {}) or V.prop_counts(end) or '—'}. "
            f"Персонажей в кадре: ровно {len(shot.get('cast') or [])}.")}]
        for member in shot.get("cast") or []:
            c = V.character_by_slug(store, project_id, member["id"])
            if not c:
                continue
            parts.append({"text": f"Персонаж {c['slug']} «{c['name']}». Карта: {c['identity_card']}."
                                  + (f" Ограничения: {c['constraints']}." if c.get("constraints") else "")
                                  + " Канон:"})
            sheet = _asset_path(store, V.selected_ref(store, project_id, "character", c["id"], "turnaround"))
            if sheet:
                parts.append(_inline(sheet))
        for t, path in frames:
            parts.append({"text": f"КАДР ВИДЕО, {t:g} с:"})
            parts.append(_inline(path))
        schema = {"type": "object", "properties": {
            "score": {"type": "number"}, "ok": {"type": "boolean"},
            "issues": {"type": "array", "items": {"type": "object", "properties": {
                "what": {"type": "string"}, "detail": {"type": "string"}, "at": {"type": "number"}},
                "required": ["what", "detail"]}}}, "required": ["score", "ok", "issues"]}
        response = client.generate_content(settings.gemini_text_model, {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json",
                                 "responseSchema": schema}})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    from app.ai.gemini import _extract_response_text

    try:
        data = json.loads(_extract_response_text(response))
    except json.JSONDecodeError as exc:
        raise V.AivError("Gemini вернул не-JSON — повтори проверку видео") from exc
    issues = [{"what": P._text(i.get("what"), 30), "detail": P._text(i.get("detail"), 200),
               **({"at": round(float(i["at"]), 2)} if isinstance(i.get("at"), (int, float)) else {})}
              for i in data.get("issues") or [] if isinstance(i, dict)][:10]
    score = max(0.0, min(10.0, float(data.get("score") or 0)))
    ok = bool(data.get("ok")) and score >= VIDEO_QA_MIN_SCORE and not issues
    qa = {"kind": "video_qa", "ok": ok, "score": score, "issues": issues, "frames": [t for t, _p in frames],
          "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": settings.gemini_text_model,
          "note": "; ".join(f"{i['what']}: {i['detail']}" for i in issues[:4])}
    V.set_ref_score(store, ref_id, qa, needs_review=not ok)
    return {"ok": True, "qa": qa, "needs_review": not ok,
            "note": ("видео плана с проблемами: " + (qa["note"] or f"оценка {score:g}") + " — перегенерация одной "
                     f"кнопкой (aiv_regenerate shot:{shot['id']}:shot_video), своя карточка владельцу")
            if not ok else "видео чистое"}


def video_qa_flags(store: Any, script_id: int) -> list[dict]:
    """Итоги проверки выбранных видео планов серии: план, ok, оценка, проблемы."""
    try:
        script = V.get_script(store, script_id)
    except KeyError:
        return []
    out = []
    for shot in script["shots"]:
        vid = V.selected_ref(store, int(script["project_id"]), "shot", int(shot["id"]), "shot_video")
        qa = (vid or {}).get("score") or {}
        if not vid or qa.get("kind") != "video_qa" or qa.get("error"):
            continue
        out.append({"idx": shot["idx"], "shot_id": shot["id"], "ref_id": vid["id"], "ok": bool(qa.get("ok")),
                    "score": qa.get("score"), "issues": qa.get("issues") or [], "stale": bool(vid["stale"])})
    return out


# --------------------------------------------------------------------------- #
# Постоянство склеек (Gemini-зрение, бесплатно; перед этапом video)
# --------------------------------------------------------------------------- #

CONTINUITY_PROMPT = """\
Ты монтажёр мультсериала и до оплаты видео проверяешь ключевые кадры. Проверки двух видов:
* kind "cut" — склейка: ПРОШЛЫЙ КАДР (последний кадр предыдущего плана) и СТАРТОВЫЙ КАДР этого плана,
  действие предыдущего плана (оно происходит МЕЖДУ кадрами) и заявленное состояние на старте этого
  плана (поза, куда смотрит, где в кадре, что в руках, одежда, реквизит). Смену ракурса ошибкой не считай.
* kind "pair" — пара кадров ОДНОГО плана: СТАРТ и КОНЕЦ; видеомодель будет плавно двигаться от старта
  к концу. Конец должен показывать результат ОДНОГО действия плана и заявленное конечное состояние; всё
  остальное (персонажи и их дизайн, одежда, фон, свет, ракурс, число предметов) — как на старте.
Найди несоответствия: поза/взгляд/руки/одежда/реквизит не как заявлено, без причины поменялись одежда,
реквизит, причёска, свет, локация, дизайн персонажа, лишние/пропавшие/раздвоившиеся персонажи или
предметы, персонаж или предмет висит в воздухе.
Для каждой проверки: idx плана, kind, score 0–10 (10 — чисто), ok, mismatches — список
{what: pose|facing|position|holding|clothing|props|count|character|design|floating|lighting|location|end_state,
detail — коротко по-русски}. Верни ТОЛЬКО JSON."""


def _inline(path: Path) -> dict:
    import base64

    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(path.read_bytes()).decode("ascii")}}


def _save_continuity(store: Any, shot_id: int, data: dict) -> None:
    store.db.execute("UPDATE aiv_shots SET continuity_json = ? WHERE id = ?",
                     (json.dumps(data, ensure_ascii=False), int(shot_id)))


def check_continuity(store: Any, script_id: int, client: Any = None) -> dict:
    """Ключевые кадры серии ДО видео: склейки (стартовый кадр плана против конца прошлого и
    заявленного состояния; общий кадр сцепки совпадает по построению) и пары кадров планов (старт
    против конца: одно действие, конечное состояние, тот же дизайн и счёт). Результат — на
    раскадровке (``shot.continuity``) и в карточке этапа video; платное само не перезапускается."""
    script = V.get_script(store, script_id)
    project_id = int(script["project_id"])
    if client is None and not settings.gemini_api_key:
        return {"ok": True, "skipped": True, "shots": [],
                "note": "Gemini не настроен — проверка кадров пропущена (сравни кадры глазами: aiv_get_script)"}
    shots = script["shots"]
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    checks: list[tuple[dict, str]] = []  # (план, kind)
    entries: dict[int, dict] = {}  # shot_id → итог
    parts: list[dict] = [{"text": CONTINUITY_PROMPT}]
    for n, shot in enumerate(shots):
        prev = shots[n - 1] if n else None
        nxt = shots[n + 1] if n + 1 < len(shots) else None
        start, shared = start_frame_ref(store, project_id, shots, n)
        end = end_frame_ref(store, project_id, shot)
        entry = {"ok": True, "score": None, "mismatches": [], "checked_at": stamp,
                 "start_ref_id": start["id"] if start else None, "end_ref_id": end["id"] if end else None}
        start_path, end_path = _asset_path(store, start), _asset_path(store, end)
        if prev is not None and shared:
            entry.update({"chain": True, "note": f"старт — конечный кадр плана {prev['idx']} (общий кадр)"})
        elif prev is not None and start_path is not None:
            prev_frame, prev_kind = _prev_frame(store, project_id, prev)
            if prev_frame is not None:
                entry["against"] = prev_kind
                checks.append((shot, "cut"))
                prev_state = V.state_text(store, project_id, V.end_state_of(prev, shot)) or "не задано"
                state = V.state_text(store, project_id, shot.get("state") or {}) or "не задано"
                parts.append({"text": f"ПРОВЕРКА idx={shot['idx']} kind=cut (продолжает прошлый: "
                                      f"{'да' if shot.get('continues_from_prev') else 'нет, новый момент/ракурс'}). "
                                      f"Действие предыдущего плана: {prev.get('action') or '—'}. Конец предыдущего: "
                                      f"{prev_state}. Заявленное состояние на старте этого: {state}. ПРОШЛЫЙ КАДР:"})
                parts.append(_inline(prev_frame))
                parts.append({"text": "СТАРТОВЫЙ КАДР:"})
                parts.append(_inline(start_path))
        if start_path is not None and end_path is not None:
            checks.append((shot, "pair"))
            parts.append({"text": f"ПРОВЕРКА idx={shot['idx']} kind=pair. Одно действие плана: "
                                  f"{shot.get('action') or '—'}. Конечное состояние: "
                                  f"{V.state_text(store, project_id, V.end_state_of(shot, nxt)) or 'не задано'}. "
                                  f"Реквизит: {V.prop_counts(shot.get('state') or {}) or '—'}. СТАРТ:"})
            parts.append(_inline(start_path))
            parts.append({"text": "КОНЕЦ:"})
            parts.append(_inline(end_path))
        if prev is not None or end_path is not None:
            entries[int(shot["id"])] = entry
    if checks:
        schema = {"type": "object", "properties": {"checks": {"type": "array", "items": {
            "type": "object", "properties": {
                "idx": {"type": "integer"}, "kind": {"type": "string", "enum": ["cut", "pair"]},
                "score": {"type": "number"}, "ok": {"type": "boolean"},
                "mismatches": {"type": "array", "items": {"type": "object", "properties": {
                    "what": {"type": "string"}, "detail": {"type": "string"}}, "required": ["what", "detail"]}}},
            "required": ["idx", "kind", "score", "ok", "mismatches"]}}}, "required": ["checks"]}
        from app.ai.gemini import GeminiClient, _extract_response_text

        payload = {"contents": [{"role": "user", "parts": parts}],
                   "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json",
                                        "responseSchema": schema}}
        response = (client or GeminiClient()).generate_content(settings.gemini_text_model, payload)
        try:
            data = json.loads(_extract_response_text(response))
        except json.JSONDecodeError as exc:
            raise V.AivError("Gemini вернул не-JSON — повтори проверку кадров") from exc
        items = data.get("checks") if isinstance(data.get("checks"), list) else data.get("shots") or []
        got = {(int(i.get("idx") or 0), str(i.get("kind") or "cut")): i for i in items if isinstance(i, dict)}
        for shot, kind in checks:
            item = got.get((int(shot["idx"]), kind))
            if item is None:
                continue
            entry = entries[int(shot["id"])]
            mism = [{"what": P._text(m.get("what"), 30), "detail": P._text(m.get("detail"), 200), "check": kind}
                    for m in item.get("mismatches") or [] if isinstance(m, dict)][:8]
            score = max(0.0, min(10.0, float(item.get("score") or 0)))
            entry["mismatches"] += mism
            entry["score"] = score if entry["score"] is None else min(entry["score"], score)
            entry["ok"] = entry["ok"] and bool(item.get("ok")) and score >= 6 and not mism
            entry.setdefault("checks", []).append(kind)
            entry["model"] = settings.gemini_text_model
    results = []
    for shot in shots:
        entry = entries.get(int(shot["id"]))
        if entry is None or (not entry.get("checks") and not entry.get("chain")):
            continue
        _save_continuity(store, shot["id"], entry)
        results.append({"idx": shot["idx"], **entry})
    flagged = [r["idx"] for r in results if not r.get("ok")]
    return {"ok": True, "shots": results, "flagged": flagged,
            "note": ("кадры с несоответствиями: " + ", ".join(f"план {i}" for i in flagged)
                     + " — перерисуй кадр (aiv_regenerate shot:ID, дёшево) или поправь state/действие; "
                       "платное само не перезапускается") if flagged else "склейки и пары кадров чистые"}


def continuity_flags(store: Any, script_id: int) -> list[dict]:
    """Сохранённые проверки кадров: план, ok, что не так, устарела ли (кадр с тех пор сменился)."""
    try:
        script = V.get_script(store, script_id)
    except KeyError:
        return []
    out = []
    shots = script["shots"]
    pid = int(script["project_id"])
    for n, shot in enumerate(shots):
        data = shot.get("continuity")
        if not data:
            continue
        start = start_frame_ref(store, pid, shots, n)[0]
        end = end_frame_ref(store, pid, shot)
        outdated = any(data.get(key) and (not ref or int(ref["id"]) != int(data[key]))
                       for key, ref in (("start_ref_id", start), ("end_ref_id", end)))
        out.append({"idx": shot["idx"], "shot_id": shot["id"], "ok": bool(data.get("ok")), "score": data.get("score"),
                    "mismatches": data.get("mismatches") or [], "outdated": outdated,
                    "chain": bool(data.get("chain")), "checks": data.get("checks") or []})
    return out


# --------------------------------------------------------------------------- #
# Работник
# --------------------------------------------------------------------------- #


def kick() -> None:
    if _wake is not None:
        _wake.set()


class AiVideoWorker:
    """Фон: после «Разрешить» запускает одобренные запуски, после перезапуска дочитывает опрос.
    Лёгкий, сетевой, отдельный от heavy_queue. ``AIVIDEO_WORKER=0`` — не запускать."""

    IDLE_SECONDS = 30

    def __init__(self, store: Any) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import os

        global _wake
        if os.getenv("AIVIDEO_WORKER", "1").strip().lower() in ("0", "false", "no", "off"):
            return
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        self._stop = threading.Event()
        _wake = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop, _wake), name="aivideo-gen", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if _wake is not None:
            _wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self, stop: threading.Event, wake: threading.Event) -> None:
        while not stop.is_set():
            try:
                busy = self.store.db.query_one(
                    "SELECT 1 FROM aiv_gen_jobs WHERE status IN ('approved', 'polling', 'submitting', 'recovering') "
                    "LIMIT 1")
                if busy:
                    run_pending(self.store)
            except Exception as exc:  # noqa: BLE001 - фон не умирает
                _log.warning("aivideo worker pass failed: %s", exc)
            wake.wait(self.IDLE_SECONDS)
            wake.clear()
