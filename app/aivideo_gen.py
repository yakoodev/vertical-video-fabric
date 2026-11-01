"""ИИ-видео: этапы генерации, деньги и одобрение (docs/design/ai-video-projects.md §3.5–3.6).

Этапы (решение владельца 2026-11-01 — одобрение ПО ЭТАПАМ):

* ``refs`` — стиль-библия (картинки настроения), плиты локаций, листы персонажей (разворот,
  эмоции, облики);
* ``keyframes`` — кадр на каждый план серии (референсы персонажей и локации передаются модели);
* ``voice`` — озвучка реплик голосами персонажей (TTS из ``app/dubbing.py``);
* (v2) ``video`` — оживление кадров видеомоделью.

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

Двойной оплаты нет: ``remote_id`` пишется в БД ДО опроса, после перезапуска опрос продолжается
(``resume``) без нового POST; запуск, оборванный между «отправляю» и ответом провайдера, не
повторяется сам (``failed``: «проверь баланс»). Одинаковый запрос (``input_hash``) берёт готовый
результат. Каждое списание — строка ``aiv_ledger`` (факт провайдера, разница баланса или оценка).
"""

from __future__ import annotations

import hashlib
import json
import logging
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
    ProviderError,
    SubmitUncertain,
    image_price,
    max_refs,
    model_params,
    video_params,
    video_price,
    video_seconds_range,
)
from app.settings import settings

_log = logging.getLogger(__name__)

STAGES = ("refs", "keyframes", "voice", "video")
STAGE_LABELS = {"refs": "референсы (стиль, локации, листы персонажей)", "keyframes": "кадры планов",
                "voice": "озвучка реплик", "video": "видео планов (оживление кадров)"}
EPISODE_CAP_RUB = {"draft": 350.0, "quality": 2500.0}
STYLE_REF_COUNT = 3
CARD_ACTION = "aiv_budget"
# recovering — POST ушёл без ответа (504/обрыв): id ищем в истории Polza, POST не повторяем.
ACTIVE = ("approved", "submitting", "polling", "recovering")
VIDEO_MIN_SEC = 4  # план-видео 4–8 с (у seedance меньше 4 не бывает)
VIDEO_MAX_SEC = 8
# Поиск id в истории после 504: сразу RECOVER_ATTEMPTS раз через RECOVER_INTERVAL_SEC, дальше —
# каждым проходом работника; не нашли за RECOVER_DEADLINE_SEC — failed (скорее всего не списано).
RECOVER_ATTEMPTS = 6
RECOVER_INTERVAL_SEC = 10.0
RECOVER_DEADLINE_SEC = 20 * 60
OPEN = ("awaiting",) + ACTIVE
STYLE_BASE = {
    "anime": "2D anime / toon style, clean line art, cel shading, flat colors, expressive faces",
    "photoreal": "photorealistic, cinematic lighting, natural skin texture, 35mm film still",
}
NO_TEXT = "No text, no letters, no captions, no watermark, no logo."
REF_ASPECT = {"style": "9:16", "location": "9:16", "turnaround": "16:9", "expressions": "3:2", "look": "9:16",
              "keyframe": "9:16"}
REF_LABELS = {"style": "стиль", "location": "плита локации", "turnaround": "разворот", "expressions": "эмоции",
              "look": "облик", "keyframe": "кадр", "line_audio": "реплика", "shot_video": "видео плана"}

_LOCK = threading.RLock()
_ACTIVE_JOBS: set[int] = set()  # опрашиваются сейчас (не опрашивать вторым потоком)
_INFLIGHT: set[int] = set()  # взяты в работу этим процессом (resume их не трогает)
_wake: threading.Event | None = None


class BudgetError(V.AivError):
    """Этап дороже потолка / выключено / нет одобрения — ничего не запущено."""


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


def _reserved(store: Any, where: str, params: tuple, statuses: tuple = OPEN, exclude_approval: int | None = None) -> float:
    marks = ", ".join("?" for _ in statuses)
    sql = (f"SELECT COALESCE(SUM(estimate_rub), 0) FROM aiv_gen_jobs WHERE status IN ({marks}) AND provider != 'none' "
           f"AND {where}")
    args = (*statuses, *params)
    if exclude_approval is not None:
        sql += " AND COALESCE(approval_id, 0) != ?"
        args = (*args, int(exclude_approval))
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
                 exclude_approval: int | None = None, reserved: bool = True) -> list[str]:
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


def _voice_of(store: Any, project_id: int, who: str) -> dict:
    style = V._style_row(store, project_id)
    character = V.character_by_slug(store, project_id, who) if who and who != "narrator" else None
    voice = dict((character or {}).get("voice") or {})
    provider = "none" if style["provider"] == "none" else (voice.get("provider") or "polza")
    return {"provider": provider, "model": voice.get("model") or settings.polza_tts_model,
            "voice": voice.get("voice") or settings.polza_tts_voice}


def _line_key(voice: dict, text: str) -> str:
    return f"{voice['provider']}|{voice['model']}|{voice['voice']}|{text}"


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

    stage = P._choice(stage, STAGES, "stage")
    style = V.get_style(store, project_id)
    provider, model = style["provider"], style["image_model"]
    quality = _quality(store, project_id, script_id)
    if stage in ("keyframes", "voice", "video"):
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
            for look in c["looks"]:
                ref = V.selected_ref(store, project_id, "look", look["id"], "look")
                if need(ref, "look", look["id"], "look"):
                    out.append(_image_item(stage, "look", look["id"], "look", f"{c['name']}: облик «{look['name']}»",
                                           model, quality, provider))
    elif stage == "keyframes":
        for shot in script["shots"]:
            if shot["locked"] and not explicit:
                continue
            ref = V.selected_ref(store, project_id, "shot", shot["id"], "keyframe")
            if need(ref, "shot", shot["id"], "keyframe"):
                item = _image_item(stage, "shot", shot["id"], "keyframe", f"план {shot['idx']}", model, quality,
                                   provider)
                out.append({**item, "script_id": int(script_id)})
    elif stage == "video":
        out += _plan_video(store, project_id, script, style, explicit, want, need)
    else:  # voice
        for shot in script["shots"]:
            for i, line in enumerate(shot["dialogue"]):
                voice = _voice_of(store, project_id, line["who"])
                ref = V.selected_ref(store, project_id, "line", shot["id"], "line_audio", str(i))
                current = _fresh(ref) and ref["prompt"] == _line_key(voice, line["text"])
                hit = want("line", shot["id"], "line_audio", str(i))
                if hit is False or (hit is None and current and not regenerate):
                    continue
                tts = get_tts(voice["provider"], voice["model"], voice["voice"])
                price = tts.price_rub_per_char() if getattr(tts, "paid", False) else 0.0
                out.append({"stage": stage, "kind": "tts", "target_type": "line", "target_id": int(shot["id"]),
                            "ref_kind": "line_audio", "slot": str(i), "script_id": int(script_id),
                            "label": f"план {shot['idx']}, реплика {i + 1} ({line['who']})",
                            "provider": voice["provider"], "model": voice["model"], "voice": voice["voice"],
                            "text": line["text"], "chars": len(line["text"]),
                            "estimate_rub": round(len(line["text"]) * price, 2),
                            "price_note": "сухой прогон" if not price else "цена TTS из app/dubbing.py"})
    for item in out:
        item.setdefault("script_id", script_id)
        item["regenerate"] = bool(regenerate or explicit)
    return out


def _is_chain(shot: dict, prev: dict | None) -> bool:
    """План продолжает прошлый, у которого будет видео: старт — последний кадр того видео (0 ₽)."""
    return bool(prev and shot.get("continues_from_prev") and prev.get("needs_motion"))


def _fresh_video(store: Any, project_id: int, shot: dict | None) -> dict | None:
    if not shot:
        return None
    ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), "shot_video")
    return ref if _fresh(ref) else None


def _chain_item(shot: dict, prev: dict, script_id: int) -> dict:
    return {"stage": "video", "kind": "chain", "target_type": "shot", "target_id": int(shot["id"]),
            "ref_kind": "keyframe", "slot": "", "script_id": int(script_id),
            "label": f"план {shot['idx']}: старт — последний кадр видео плана {prev['idx']}", "provider": "none",
            "model": "", "estimate_rub": 0.0, "price_note": "0 ₽ — кадр из готового видео"}


def _plan_video(store: Any, project_id: int, script: dict, style: dict, explicit: bool, want: Any,
                need: Any) -> list[dict]:
    """Видео планов с needs_motion по порядку. Сцепка: план продолжает прошлый — перед его видео
    бесплатный запуск ``chain`` (последний кадр видео прошлого плана → кадр плана). Иначе старт —
    выбранный кадр плана (этап keyframes); нет кадра — элемент ``blocked``."""
    provider, model, res = style["video_provider"], style["video_model"], style["video_resolution"]
    out: list[dict] = []
    planned: set[int] = set()
    shots = script["shots"]
    for n, shot in enumerate(shots):
        prev = shots[n - 1] if n else None
        chain = _is_chain(shot, prev)
        prev_video = _fresh_video(store, project_id, prev)
        prev_new = prev is not None and int(prev["id"]) in planned
        kf = V.selected_ref(store, project_id, "shot", int(shot["id"]), "keyframe")
        if not shot["needs_motion"]:
            # Статичный план после видео той же сцены: его кадр — последний кадр того видео.
            if chain and (prev_new or (prev_video and (kf or {}).get("prompt") != f"chain:{prev_video['id']}")):
                if want("shot", int(shot["id"]), "keyframe") is not False or prev_new:
                    out.append(_chain_item(shot, prev, script["id"]))
            continue
        if shot["locked"] and not explicit:
            continue
        ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), "shot_video")
        if not need(ref, "shot", int(shot["id"]), "shot_video"):
            continue
        if chain and (prev_new or prev_video):
            out.append(_chain_item(shot, prev, script["id"]))
            start = "chain"
        elif kf and kf.get("asset_id"):
            start = "keyframe"
        else:
            out.append({**_video_item(shot, script["id"], provider, model, res, "keyframe"),
                        "blocked": f"план {shot['idx']}: нет кадра — сначала этап keyframes"})
            continue
        out.append(_video_item(shot, script["id"], provider, model, res, start))
        planned.add(int(shot["id"]))
    return out


def estimate(store: Any, project_id: int, stage: str, script_id: int | None = None, items: list | None = None,
             regenerate: bool = False) -> dict:
    """Оценка этапа (бесплатно, ничего не запускает)."""
    V.require_project(store, project_id)
    raw = plan_stage(store, project_id, stage, script_id, items, regenerate)
    planned = [i for i in raw if not i.get("blocked")]
    blocked = [i["blocked"] for i in raw if i.get("blocked")]
    total = round(sum(i["estimate_rub"] for i in planned), 2)
    paid = any(i["provider"] != "none" for i in planned)
    problems = cap_problems(store, project_id, script_id, total, stage) if paid else []
    problems += blocked
    if paid and not settings.aivideo_enabled:
        problems.append("платная генерация выключена (AIVIDEO_ENABLED=0) — включает владелец; карточку можно "
                        "отправить заранее, запуск — после включения")
    style = V.get_style(store, project_id)
    if stage == "video":
        provider, model = style["video_provider"], style["video_model"]
    else:
        provider, model = style["provider"], style["image_model"]
    if stage == "voice":
        model = "TTS"
    measured = all(i.get("price_note", "").startswith(("замер", "0 ₽", "сухой")) for i in planned)
    note = (f"{STAGE_LABELS[stage]}: {len(planned)} шт. ≈ {total:.2f} ₽"
            + ((f" ({model}, цены от {PRICE_CHECKED}" + (", замер)" if measured else ", ×2 к прайсу — не замерено)"))
               if paid else " — сухой прогон, 0 ₽"))
    if stage == "video" and paid and any("потолка серии" in p for p in problems):
        problems.append(f"подсказка: серия-черновик ≤ {EPISODE_CAP_RUB['draft']:.0f} ₽ — для видео переключи серию "
                        "в quality (aiv_update_script quality) или возьми дешёвую модель (video_model "
                        "grok-imagine-video-1-5); решает владелец")
    return {"stage": stage, "stage_label": STAGE_LABELS[stage], "project_id": int(project_id), "script_id": script_id,
            "items": planned, "count": len(planned), "total_rub": total, "paid": paid, "blocked": blocked,
            "provider": provider, "model": model, "problems": problems,
            "budget": budget_view(store, project_id, script_id), "note": note}


# --------------------------------------------------------------------------- #
# Запрос этапа → карточка владельцу
# --------------------------------------------------------------------------- #


def _insert_job(store: Any, project_id: int, item: dict, status: str, approval_id: int | None,
                requested_by: str) -> int:
    params = {"resolution": item.get("resolution"), "aspect": item.get("aspect"), "voice": item.get("voice"),
              "text": item.get("text"), "regenerate": item.get("regenerate", False), "seconds": item.get("seconds"),
              "start": item.get("start")}
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
    lines = [f"ИИ-видео «{project['name']}»" + (f", серия {script['episode_no']} «{script['title']}»" if script else "")
             + f": {est['stage_label']} — {est['count']} шт. ≈ {est['total_rub']:.2f} ₽ "
             f"({est['model'] if est['stage'] != 'voice' else 'TTS'})."]
    month = bud["month"]
    lines.append(f"Месяц проекта: потрачено {month['spent_rub']:.0f} из {month['cap_rub']:.0f} ₽"
                 + (f"; серия: {bud['episode']['spent_rub']:.0f} из {bud['episode']['cap_rub']:.0f} ₽"
                    if bud.get("episode") else "") + ".")
    names = [i["label"] for i in est["items"][:8]]
    lines.append("Что: " + "; ".join(names) + (f" и ещё {est['count'] - 8}" if est["count"] > 8 else ""))
    if not settings.aivideo_enabled:
        lines.append("⚠ AIVIDEO_ENABLED=0 — запустится после включения владельцем.")
    lines.append("Разрешить эту сумму?")
    return "\n".join(lines)


def request_stage(store: Any, project_id: int, stage: str, script_id: int | None = None, items: list | None = None,
                  regenerate: bool = False, requested_by: str = "agent", note: str = "") -> dict:
    """Этап: сухой прогон — сразу (0 ₽); платно — карточка владельцу с суммой (жди решения)."""
    project = V.require_project(store, project_id)
    est = estimate(store, project_id, stage, script_id, items, regenerate)
    if est["blocked"]:
        raise V.AivError("этап не запрошен: " + "; ".join(est["blocked"]))
    if not est["items"]:
        return {"ok": True, "status": "nothing", "estimate": est,
                "note": "всё уже есть и не устарело — для новой версии укажи items (перегенерация)"}
    if not est["paid"]:
        job_ids = [_insert_job(store, project_id, item, "approved", None, requested_by) for item in est["items"]]
        result = run_jobs(store, job_ids)
        return {"ok": True, "status": "done", "estimate": est, "run": result,
                "note": "сухой прогон: заглушки вместо картинок, тон вместо голоса, 0 ₽"}
    hard = [p for p in est["problems"] if "AIVIDEO_ENABLED" not in p]
    if hard:
        raise BudgetError("этап не запрошен: " + "; ".join(hard))
    script = V.get_script(store, script_id, with_shots=False) if script_id is not None else None
    # Прежняя ждущая карточка того же этапа — устарела: новая заменяет её.
    for old in stage_approvals(store, project_id, script_id, status="pending"):
        if old["stage"] == stage and old.get("script_id") == script_id and not est["items"][0]["regenerate"]:
            A._update(store, old["id"], status="expired", decision_comment="заменена новой карточкой этапа")
            store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'карточка заменена' "
                             "WHERE approval_id = ? AND status = 'awaiting'", (old["id"],))
    payload = {"action": CARD_ACTION, "stage": stage, "project_id": int(project_id), "script_id": script_id,
               "amount_rub": est["total_rub"], "count": est["count"], "model": est["model"],
               "provider": est["provider"], "note": P._text(note, 300)}
    card = A.submit_card(store, project_id, "other", _card_question(store, project, est, script), payload,
                         title=f"ИИ-видео: {est['stage_label']} ≈ {est['total_rub']:.0f} ₽",
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
    if target_type == "shot":
        return ("video" if kind == "shot_video" else "keyframes"), int(V.get_shot(store, target_id)["script_id"])
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
        out.append({"id": card["id"], "status": card["status"], "stage": payload.get("stage"),
                    "script_id": payload.get("script_id"), "amount_rub": payload.get("amount_rub"),
                    "count": payload.get("count"), "model": payload.get("model"), "title": card["title"],
                    "question": card["question"], "decided_by": card.get("decided_by"),
                    "decision_comment": card.get("decision_comment"), "created_at": card.get("created_at"),
                    "spent_rub": spent_approval(store, card["id"]), "jobs": counts})
    return out


def _on_decide(store: Any, approval: dict, action: str, comment: str, actor: str) -> dict:
    if action in ("approve", "both"):
        cur = store.db.execute("UPDATE aiv_gen_jobs SET status = 'approved', updated_at = CURRENT_TIMESTAMP "
                               "WHERE approval_id = ? AND status = 'awaiting'", (approval["id"],))
        kick()
        return {"aiv": {"approved_jobs": int(cur.rowcount or 0),
                        "note": "запуски одобрены — работник ИИ-видео их выполнит"}}
    cur = store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = ?, updated_at = CURRENT_TIMESTAMP "
                           "WHERE approval_id = ? AND status = 'awaiting'",
                           (f"владелец: {action}{(' — ' + comment) if comment else ''}"[:300], approval["id"]))
    return {"aiv": {"cancelled_jobs": int(cur.rowcount or 0)}}


A.register_card_action(CARD_ACTION, [("approve", "✅ Разрешить"), ("reject", "🗑 Отказ")], _on_decide)


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


def _style_paths(store: Any, project_id: int, limit: int = 1) -> list[Path]:
    paths = []
    for ref in V.list_refs(store, project_id, "style", 0):
        if ref["kind"] in ("style", "upload") and (ref["selected"] or ref["kind"] == "upload"):
            p = _asset_path(store, ref)
            if p:
                paths.append(p)
    return paths[:limit]


def _identity(character: dict) -> str:
    return f"«{character['name']}» — identity card (verbatim, do not alter): {character['identity_card']}"


def build_request(store: Any, job: dict) -> tuple[str, list[Path], list[str]]:
    """(промпт, файлы-референсы, подпись заглушки) запуска. Карта личности — дословно."""
    project_id = int(job["project_id"])
    style = V.get_style(store, project_id)
    st = _style_text(style)
    neg = f" Avoid: {style['negative_text']}." if style.get("negative_text") else ""
    kind, target_id = job["ref_kind"], int(job["target_id"])
    refs: list[Path] = []
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
        prompt = (f"{st}. Empty background plate, no people, vertical 9:16 composition. Location «{loc['name']}»: "
                  f"{loc['description']}. Consistent with the style reference. {NO_TEXT}{neg}")
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
        look = V.get_look(store, target_id)
        c = V.get_character(store, look["character_id"])
        prompt = (f"{st}. Full-body image of the same character on a neutral background, new outfit/state. "
                  f"{_identity(c)}. Outfit/state: {look['outfit_text']}. Change ONLY clothes and state; keep face, "
                  f"hair, body proportions and colors exactly as in the reference. {NO_TEXT}{neg}")
        refs = _canon_refs(store, project_id, c)
        caption += [c["name"], look["name"]]
    elif kind == "keyframe":
        shot = V.get_shot(store, target_id)
        cam = shot["camera"]
        parts = [f"{st}. Vertical 9:16 film still (keyframe) of shot {shot['idx']}."]
        framing = ", ".join(x for x in (str(cam.get("shot") or "medium").replace("_", " ") + " shot",
                                        f"{cam['angle']} angle" if cam.get("angle") else "",
                                        f"{cam['lens']}mm lens" if cam.get("lens") else "") if x)
        parts.append(f"Camera: {framing}.")
        loc = V.location_by_slug(store, project_id, shot["location"]) if shot["location"] else None
        if loc:
            parts.append(f"Location «{loc['name']}»: {loc['description']} (match the location reference).")
            plate = V.selected_ref(store, project_id, "location", loc["id"], "location")
            p = _asset_path(store, plate)
            if p:
                refs.append(p)
            caption.append(loc["name"])
        names = []
        for member in shot["cast"]:
            c = V.character_by_slug(store, project_id, member["id"])
            if not c:
                continue
            names.append(c["name"])
            text = _identity(c)
            look = next((l for l in c["looks"] if l["slug"] == member.get("look")), None)
            if look:
                text += f"; outfit/state: {look['outfit_text']}"
                lp = _asset_path(store, V.selected_ref(store, project_id, "look", look["id"], "look"))
                if lp:
                    refs.append(lp)
            if member.get("action"):
                text += f"; doing: {member['action']}"
            parts.append(text + ".")
            refs = _canon_refs(store, project_id, c)[-2:] + refs
        emotions = ", ".join(d["emotion"] for d in shot["dialogue"] if d.get("emotion"))
        if shot["action"]:
            parts.append(f"Action: {shot['action']}.")
        if emotions:
            parts.append(f"Emotion: {emotions}.")
        if shot["image_prompt"]:
            parts.append(shot["image_prompt"])
        count = len(names)
        parts.append(f"Exactly {count} character{'s' if count != 1 else ''} in frame, no extra people. "
                     if count else "No people in frame. ")
        parts.append(NO_TEXT + neg)
        prompt = " ".join(parts)
        refs += _style_paths(store, project_id, 1)
        caption = [f"план {shot['idx']}", *(names or ["без персонажей"]), *(caption[1:])]
    else:
        raise V.AivError(f"неизвестный вид картинки {kind}")
    seen: set[str] = set()
    uniq = []
    for p in refs:
        key = str(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return prompt, uniq[:max_refs(job["model"], job["provider"])], caption


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
# Запуск
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


def _preflight(store: Any, job: dict) -> list[str]:
    """Можно ли платить за этот запуск прямо сейчас (под замком)."""
    if job["provider"] == "none":
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
    spent = spent_approval(store, card["id"])
    if spent + float(job["estimate_rub"]) > amount + 0.01:
        out.append(f"по карточке #{card['id']} потрачено {spent:.2f} ₽ + {job['estimate_rub']:.2f} ₽ > одобренных "
                   f"{amount:.2f} ₽ — нужна новая карточка")
    out += cap_problems(store, int(job["project_id"]), job.get("script_id"), float(job["estimate_rub"]), job["stage"],
                        reserved=False)
    return [p for p in out if "больше потолка одного этапа" not in p]


def _claim(store: Any, job_id: int) -> tuple[dict | None, list[str]]:
    with _LOCK:
        job = get_job(store, job_id)
        if job["status"] != "approved":
            return None, []
        problems = _preflight(store, job)
        if problems:
            _set_job(store, job_id, status="held", error="; ".join(problems)[:500])
            return None, problems
        cur = store.db.execute("UPDATE aiv_gen_jobs SET status = 'submitting', attempts = attempts + 1, "
                               "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'approved'", (int(job_id),))
        if not cur.rowcount:
            return None, []
        _INFLIGHT.add(int(job_id))
        return get_job(store, job_id), []


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


def _done(store: Any, job: dict, asset_id: int, prompt: str, cost: float, source: str, note: str = "") -> dict:
    ref = V.add_ref(store, int(job["project_id"]), job["target_type"], int(job["target_id"]), job["ref_kind"],
                    int(asset_id), prompt=prompt, job_id=int(job["id"]), slot=job.get("slot") or "")
    if job["provider"] != "none" and cost > 0:
        add_ledger(store, job, cost, source, note)
    _set_job(store, job["id"], status="done", cost_rub=round(cost, 4), cost_source=source, result_ref_id=ref["id"],
             error="")
    return ref


def _run_tts(store: Any, job: dict) -> dict:
    from app.aivideo_providers import get_tts

    params = job["params"]
    text = str(params.get("text") or "")
    voice = params.get("voice") or ""
    tts = get_tts(job["provider"], job["model"], voice)
    work = settings.tmp_dir / f"aiv-tts-{uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        raw = work / "line.mp3"
        speech = tts.synthesize(text, raw)
        cost = speech.cost_rub
        source = "provider" if cost is not None else "estimate"
        cost = float(cost) if cost is not None else float(job["estimate_rub"])
        asset = _save_asset(store, job, Path(speech.path), "audio", "audio/mpeg", f"ИИ-видео: {job['label']}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    key = _line_key({"provider": job["provider"], "model": job["model"], "voice": voice}, text)
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


def _submit(store: Any, job: dict, provider: Any, full: dict, refs: list[Path],
            poll_timeout: float | None) -> dict | None:
    """POST провайдеру. Время отправки пишется ДО запроса: ответа нет (``SubmitUncertain``: 504,
    обрыв) — запуск ``recovering``, id ищет адаптер (``recover``), POST не повторяется никогда."""
    _set_job(store, job["id"], submitted_at=_utc_now())
    try:
        remote = provider.submit(full, refs)
    except SubmitUncertain as exc:
        _set_job(store, job["id"], status="recovering",
                 error=f"{exc} — ищу запрос у провайдера, повторно не отправляю"[:500])
        return _recover(store, get_job(store, job["id"]), provider, poll_timeout, RECOVER_ATTEMPTS)
    except ProviderError as exc:
        _set_job(store, job["id"], status="blocked_policy" if exc.policy else "failed", error=str(exc)[:500])
        return None
    # ДО опроса: после перезапуска опрос продолжится, повторного POST (и оплаты) не будет.
    _set_job(store, job["id"], status="polling", remote_id=str(remote))
    return _finish(store, get_job(store, job["id"]), provider, poll_timeout)


def _claimed_remote_ids(store: Any, job_id: int) -> set[str]:
    rows = store.db.query_all("SELECT remote_id FROM aiv_gen_jobs WHERE remote_id != '' AND id != ?", (int(job_id),))
    return {str(r["remote_id"]) for r in rows}


def _recover(store: Any, job: dict, provider: Any, poll_timeout: float | None, attempts: int = 1) -> dict | None:
    """Найти у провайдера запрос, ушедший без ответа. Нашли — опрос как обычно; нет и срок вышел —
    failed (вероятно, не списано); пока ищем — ``recovering`` (работник спросит ещё)."""
    from app.aivideo_providers import parse_utc

    recover = getattr(provider, "recover", None)
    if recover is None:
        _set_job(store, job["id"], status="failed",
                 error="запрос ушёл без ответа, провайдер не умеет найти его — могло быть оплачено; проверь баланс, "
                       "повтор только вручную (перегенерация)")
        return None
    for attempt in range(max(1, attempts)):
        found = None
        try:
            found = recover(job["model"], job.get("submitted_at") or "", _claimed_remote_ids(store, job["id"]))
        except Exception as exc:  # noqa: BLE001 - сеть: спросим следующим проходом
            _log.warning("aiv recover %s: %s", job["id"], exc)
        if found and found.get("id"):
            with _LOCK:
                if str(found["id"]) in _claimed_remote_ids(store, job["id"]):
                    found = None
                else:
                    _set_job(store, job["id"], status="polling", remote_id=str(found["id"]),
                             error=f"id {found['id']} найден у провайдера после обрыва ответа — POST не повторял")
            if found:
                return _finish(store, get_job(store, job["id"]), provider, poll_timeout)
        if attempt + 1 < attempts and RECOVER_INTERVAL_SEC > 0:
            time.sleep(RECOVER_INTERVAL_SEC)
    sent = parse_utc(job.get("submitted_at"))
    if sent is not None:
        from datetime import datetime, timezone

        if (datetime.now(timezone.utc) - sent).total_seconds() > RECOVER_DEADLINE_SEC:
            _set_job(store, job["id"], status="failed",
                     error=f"за {RECOVER_DEADLINE_SEC // 60} мин запрос у провайдера не появился — скорее всего не "
                           "списано (проверь баланс); повтор только вручную (перегенерация)")
    return None


def _run_image(store: Any, job: dict, provider: Any, poll_timeout: float | None) -> dict | None:
    prompt, refs, caption = build_request(store, job)
    params = model_params(job["model"], prompt, job["params"].get("aspect") or "9:16",
                          job["params"].get("resolution") or "1K", job["provider"])
    digest = input_hash(job["model"] + "|" + job["provider"], params, prompt, refs, _variant(store, job))
    cached = _cached(store, digest)
    _set_job(store, job["id"], prompt=prompt, input_hash=digest,
             params_json=json.dumps({**job["params"], "request": {k: v for k, v in params.items() if k != "prompt"},
                                     "refs": [p.name for p in refs]}, ensure_ascii=False))
    if cached:
        return _done(store, job, int(cached["asset_id"]), prompt, 0.0, "cache", f"тот же запрос, что #{cached['id']}")
    full = {**params, "_kind": job["ref_kind"], "_caption": caption}
    return _submit(store, job, provider, full, refs, poll_timeout)


# --------------------------------------------------------------------------- #
# Видео плана и сцепка
# --------------------------------------------------------------------------- #

CAMERA_TEXT = {"static": "locked-off camera", "push_in": "slow push-in", "pull_out": "slow pull-out",
               "pan_left": "slow pan left", "pan_right": "slow pan right", "tilt_up": "slow tilt up",
               "tilt_down": "slow tilt down", "parallax_push": "gentle dolly-in", "orbit": "slow orbit around",
               "float": "subtle handheld float", "dolly_zoom": "dolly zoom", "tilt_reveal": "tilt reveal"}


def _neighbors(store: Any, shot: dict) -> tuple[dict | None, dict | None]:
    shots = V.list_shots(store, shot["script_id"])
    ids = [int(s["id"]) for s in shots]
    if int(shot["id"]) not in ids:
        return None, None
    i = ids.index(int(shot["id"]))
    return (shots[i - 1] if i else None), (shots[i + 1] if i + 1 < len(shots) else None)


def build_video_prompt(store: Any, job: dict, shot: dict | None = None) -> str:
    """Промпт image-to-video: один непрерывный план от стартового кадра, действие, движение,
    состояние на старте и (если следующий план продолжает этот) на конце, звук без речи."""
    shot = shot or V.get_shot(store, int(job["target_id"]))
    project_id = int(job["project_id"])
    style = V.get_style(store, project_id)
    _prev, nxt = _neighbors(store, shot)
    parts = [f"{STYLE_BASE.get(style['style_kind'], STYLE_BASE['anime'])} animation, vertical 9:16.",
             "One continuous shot, no cuts, no scene change, no new characters. Start exactly from the given first "
             "frame: same characters, faces, clothing, props, lighting and background."]
    if style.get("passport_text"):
        parts.append(f"Style: {style['passport_text']}.")
    move = (shot.get("camera") or {}).get("move") or "static"
    parts.append(f"Camera: {CAMERA_TEXT.get(move, move.replace('_', ' '))}.")
    for member in shot.get("cast") or []:
        c = V.character_by_slug(store, project_id, member["id"])
        if c and member.get("action"):
            parts.append(f"{c['name']}: {member['action']}.")
    if shot.get("action"):
        parts.append(f"Action: {shot['action']}.")
    if shot.get("motion_prompt"):
        parts.append(f"Motion: {shot['motion_prompt']}.")
    start_state = V.state_text(store, project_id, shot.get("state") or {}) if hasattr(V, "state_text") else ""
    if start_state:
        parts.append(f"At the start: {start_state}. Keep it unless the action changes it.")
    if nxt and nxt.get("continues_from_prev"):
        end_state = V.state_text(store, project_id, nxt.get("state") or {}) if hasattr(V, "state_text") else ""
        if end_state:
            parts.append(f"By the last frame (the next shot continues from it): {end_state}.")
    emotions = ", ".join(d["emotion"] for d in shot.get("dialogue") or [] if d.get("emotion"))
    if shot.get("dialogue"):
        parts.append("The character talks" + (f" ({emotions})" if emotions else "") + ", natural mouth movement.")
    sound = shot.get("sound") or ""
    parts.append((f"Sound: {sound}. " if sound else "Sound: natural ambient room tone and effects of the action. ")
                 + "No music, no speech, no voice-over.")
    parts.append(NO_TEXT)
    return " ".join(parts)[:4000]


def _run_video(store: Any, job: dict, provider: Any, poll_timeout: float | None) -> dict | None:
    """Видео плана из выбранного кадра (его делает этап keyframes или сцепка ``chain``)."""
    shot = V.get_shot(store, int(job["target_id"]))
    kf = V.selected_ref(store, int(job["project_id"]), "shot", int(shot["id"]), "keyframe")
    start = _asset_path(store, kf)
    if start is None:
        _set_job(store, job["id"], status="failed", error="нет стартового кадра плана — этап keyframes или сцепка")
        return None
    prompt = build_video_prompt(store, job, shot)
    seconds = int(job["params"].get("seconds") or video_seconds(shot, job["model"]))
    params = video_params(job["model"], prompt, seconds, job["params"].get("resolution") or "720p",
                          job["params"].get("aspect") or "9:16", job["provider"])
    digest = input_hash(job["model"] + "|" + job["provider"], params, prompt, [start], _variant(store, job))
    cached = _cached(store, digest)
    _set_job(store, job["id"], prompt=prompt, input_hash=digest,
             params_json=json.dumps({**job["params"], "request": {k: v for k, v in params.items() if k != "prompt"},
                                     "start_ref_id": kf["id"], "refs": [start.name]}, ensure_ascii=False))
    if cached:
        return _done(store, job, int(cached["asset_id"]), prompt, 0.0, "cache", f"тот же запрос, что #{cached['id']}")
    full = {**params, "_kind": "shot_video", "_start": str(start)}
    return _submit(store, job, provider, full, [start], poll_timeout)


WAIT = object()  # сцепка ждёт видео прошлого плана (оно ещё делается)


def _run_chain(store: Any, job: dict) -> Any:
    """Старт плана = последний кадр видео прошлого плана (0 ₽). Видео прошлого ещё делается — WAIT."""
    from app.aivideo_providers import ProviderError as _PE
    from app.aivideo_providers import last_frame

    shot = V.get_shot(store, int(job["target_id"]))
    prev, _nxt = _neighbors(store, shot)
    if prev is None:
        _set_job(store, job["id"], status="failed", error="у первого плана нет прошлого — сцепка невозможна")
        return None
    busy = store.db.query_one(
        "SELECT id FROM aiv_gen_jobs WHERE kind = 'video' AND target_type = 'shot' AND target_id = ? "
        f"AND status IN ({', '.join('?' for _ in ACTIVE)}) LIMIT 1", (int(prev["id"]), *ACTIVE))
    if busy:
        return WAIT
    project_id = int(job["project_id"])
    vref = _fresh_video(store, project_id, prev)
    if vref is None:
        own = V.selected_ref(store, project_id, "shot", int(shot["id"]), "keyframe")
        if own and own.get("asset_id"):
            _set_job(store, job["id"], status="done", result_ref_id=own["id"], cost_rub=0.0, cost_source="free",
                     error=f"видео плана {prev['idx']} нет — старт остаётся свой кадр плана")
            return own
        _set_job(store, job["id"], status="failed", error=f"видео плана {prev['idx']} нет, своего кадра тоже")
        return None
    marker = f"chain:{vref['id']}"
    current = V.selected_ref(store, project_id, "shot", int(shot["id"]), "keyframe")
    if current and current.get("prompt") == marker and current.get("asset_id"):
        _set_job(store, job["id"], status="done", result_ref_id=current["id"], cost_rub=0.0, cost_source="free",
                 error="")
        return current
    src = _asset_path(store, vref)
    if src is None:
        _set_job(store, job["id"], status="failed", error=f"файла видео плана {prev['idx']} нет")
        return None
    tmp = settings.tmp_dir / f"aiv-last-{uuid4().hex}.jpg"
    try:
        last_frame(src, tmp)
    except _PE as exc:
        _set_job(store, job["id"], status="failed", error=str(exc)[:500])
        return None
    asset = _save_asset(store, job, tmp, "image", "image/jpeg",
                        f"ИИ-видео: план {shot['idx']} — последний кадр плана {prev['idx']}")
    return _done(store, job, asset["id"], marker, 0.0, "free")


def _after_video(store: Any, job: dict) -> None:
    """Новое видео плана → сцепленный следующий план (его старт и видео) «устарел»."""
    shot = V.get_shot(store, int(job["target_id"]))
    _prev, nxt = _neighbors(store, shot)
    if nxt and nxt.get("continues_from_prev"):
        store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? "
                         "AND (kind = 'shot_video' OR (kind = 'keyframe' AND prompt LIKE 'chain:%'))", (int(nxt["id"]),))


def _finish(store: Any, job: dict, provider: Any, poll_timeout: float | None) -> dict | None:
    from app.aivideo_providers import poll

    with _LOCK:
        if job["id"] in _ACTIVE_JOBS:
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
            return None
        cost = result.get("cost_rub")
        if result.get("status") == "failed":
            charged = float(cost) if cost is not None else (float(job["estimate_rub"]) if provider.paid else 0.0)
            if provider.paid and charged > 0:
                add_ledger(store, job, charged, "provider" if cost is not None else "estimate",
                           "отказ провайдера — списание по " + ("ответу" if cost is not None else
                                                              "оценке (проверь баланс)"))
            _set_job(store, job["id"], status="blocked_policy" if result.get("policy") else "failed",
                     error=str(result.get("error") or "отказ")[:500], cost_rub=charged)
            return None
        video = job["kind"] == "video"
        tmp = settings.tmp_dir / f"aiv-{uuid4().hex}{'.mp4' if video else '.jpg'}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        if video:
            start = _asset_path(store, V.get_ref(store, int(job["params"]["start_ref_id"]))) \
                if job["params"].get("start_ref_id") else None
            fetch_params = {**job["params"].get("request", {}), "_kind": "shot_video",
                            "_start": str(start) if start else ""}
        else:
            fetch_params = {**job["params"].get("request", {}), "aspect_ratio": job["params"].get("aspect"),
                            "_kind": job["ref_kind"], "_caption": build_request(store, job)[2]}
        try:
            provider.fetch(result, tmp, fetch_params)
        except Exception as exc:  # noqa: BLE001 - ссылка может истечь: оплачено, но файла нет
            _set_job(store, job["id"], status="failed", error=f"результат не скачан: {exc}"[:500])
            if provider.paid:
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
        ref = _done(store, get_job(store, job["id"]), asset["id"], job["prompt"], amount, source)
        if video:
            _after_video(store, job)
        return ref
    finally:
        with _LOCK:
            _ACTIVE_JOBS.discard(job["id"])


def _provider_for(job: dict, cache: dict) -> Any:
    from app.aivideo_providers import get_image_provider, get_video_provider

    key = (job["kind"], job["provider"])
    if key not in cache:
        cache[key] = (get_video_provider if job["kind"] == "video" else get_image_provider)(job["provider"])
    return cache[key]


def run_jobs(store: Any, job_ids: list[int], poll_timeout: float | None = None) -> dict:
    """Выполнить запуски по порядку. Упёрлись в потолок — остальные этой карточки ждут (held).
    Сцепка ждёт видео прошлого плана — она и остальные запуски её карточки остаются ``approved``
    до следующего прохода работника (видео прошлого плана дочитает ``resume``)."""
    done, failed, held, pending, waiting = [], [], [], [], []
    providers: dict[Any, Any] = {}
    stop_cards: set[int] = set()
    deferred: set[int] = set()
    for job_id in job_ids:
        job0 = get_job(store, job_id)
        card = int(job0.get("approval_id") or 0)
        if card in deferred:
            waiting.append(job_id)
            continue
        if job0.get("approval_id") in stop_cards:
            _set_job(store, job_id, status="held", error="партия остановлена: упёрлись в потолок или ошибку")
            held.append(job_id)
            continue
        job, problems = _claim(store, job_id)
        if job is None:
            if problems:
                held.append(job_id)
                if job0.get("approval_id"):
                    stop_cards.add(int(job0["approval_id"]))
            continue
        try:
            if job["kind"] == "tts":
                ref = _run_tts(store, job)
            elif job["kind"] == "chain":
                ref = _run_chain(store, job)
                if ref is WAIT:
                    _set_job(store, job_id, status="approved", attempts=max(0, int(job["attempts"]) - 1),
                             error="жду видео прошлого плана")
                    deferred.add(card)
                    waiting.append(job_id)
                    continue
            elif job["kind"] == "video":
                ref = _run_video(store, job, _provider_for(job, providers), poll_timeout)
            else:
                ref = _run_image(store, job, _provider_for(job, providers), poll_timeout)
        except Exception as exc:  # noqa: BLE001 - один запуск не валит партию
            _log.warning("aiv job %s failed: %s", job_id, exc)
            # Есть remote_id — не трогаем: опрос дочитает resume (без нового POST).
            current = get_job(store, job_id)
            if not current["remote_id"] and current["status"] != "recovering":
                hint = " (могло быть оплачено — проверь баланс; сам не повторяю)" if job["provider"] != "none" else ""
                _set_job(store, job_id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:350]}{hint}")
            ref = None
        finally:
            with _LOCK:
                _INFLIGHT.discard(int(job_id))
        state = get_job(store, job_id)["status"]
        if ref is not None:
            done.append({"job_id": job_id, "ref_id": ref["id"], "label": job["label"], "url": ref["url"]})
        elif state in ("polling", "recovering"):
            pending.append(job_id)
        else:
            failed.append({"job_id": job_id, "label": job["label"], "status": state,
                           "error": get_job(store, job_id)["error"]})
            if job.get("approval_id") and job["provider"] != "none":
                stop_cards.add(int(job["approval_id"]))
    return {"done": done, "failed": failed, "held": held, "polling": pending, "waiting": waiting}


def run_pending(store: Any, approval_id: int | None = None, project_id: int | None = None,
                poll_timeout: float | None = None) -> dict:
    """Работник: дочитать опрос (без нового POST) и выполнить одобренные запуски."""
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
                                                          "waiting": []}
    out["resumed"] = resumed
    return out


def run_approved(store: Any, approval_id: int, confirm: bool = False) -> dict:
    """Агент/REST: выполнить или продолжить запуски ОДОБРЕННОЙ владельцем карточки (обычно это делает
    работник сам сразу после «Разрешить»). Без ``confirm`` — только что будет запущено."""
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
    store.db.execute("UPDATE aiv_gen_jobs SET status = 'approved', error = '' WHERE approval_id = ? AND status = 'held'",
                     (int(approval_id),))
    return {"ok": True, "status": "ran", **run_pending(store, approval_id=approval_id)}


def resume(store: Any, poll_timeout: float | None = None) -> dict:
    """После перезапуска и каждым проходом работника:

    * ``polling`` — опрашиваем дальше (POST не повторяем);
    * ``recovering`` — ответа на POST не было: спрашиваем адаптер (``recover``), нашёлся ли запрос;
    * ``submitting`` без remote_id: время отправки записано — запрос мог уйти → ищем как
      ``recovering``; не записано — POST не начинался → снова ``approved``; у озвучки (синхронный
      платный вызов) — НЕ повторяем сами (могли уже заплатить)."""
    from app.aivideo_providers import get_image_provider, get_video_provider

    with _LOCK:
        busy = set(_ACTIVE_JOBS) | set(_INFLIGHT)
    lost, requeued = [], []
    for row in store.db.query_all("SELECT id FROM aiv_gen_jobs WHERE status = 'submitting' AND remote_id = ''"):
        if int(row["id"]) in busy:
            continue  # отправляется прямо сейчас в другом потоке этого процесса
        job = get_job(store, int(row["id"]))
        if job["kind"] in ("image", "video") and job.get("submitted_at"):
            _set_job(store, job["id"], status="recovering",
                     error="прервано после отправки — ищу запрос у провайдера, повторно не отправляю")
        elif job["kind"] in ("image", "video", "chain"):
            _set_job(store, job["id"], status="approved", error="прервано до отправки — запущу снова (не оплачено)")
            requeued.append(job["id"])
        else:
            _set_job(store, job["id"], status="failed",
                     error="прервано при отправке — могло быть оплачено; проверь баланс, повтор только вручную "
                           "(перегенерация)")
            lost.append(job["id"])

    def adapter(job: dict) -> Any:
        return (get_video_provider if job["kind"] == "video" else get_image_provider)(job["provider"])

    finished, recovered = [], []
    for row in store.db.query_all("SELECT id FROM aiv_gen_jobs WHERE status = 'recovering' "
                                  "ORDER BY submitted_at, id"):
        if int(row["id"]) in busy:
            continue
        job = get_job(store, int(row["id"]))
        ref = _recover(store, job, adapter(job), poll_timeout, attempts=1)
        state = get_job(store, job["id"])
        if state["status"] == "failed" and "не умеет" in state["error"]:
            lost.append(job["id"])
        if state["remote_id"]:
            recovered.append(job["id"])
        if ref is not None:
            finished.append(job["id"])
    for row in store.db.query_all("SELECT id FROM aiv_gen_jobs WHERE status = 'polling' AND remote_id != '' ORDER BY id"):
        if int(row["id"]) in busy or int(row["id"]) in finished or int(row["id"]) in recovered:
            continue
        job = get_job(store, int(row["id"]))
        ref = _finish(store, job, adapter(job), poll_timeout)
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
    if ref["kind"] != "keyframe":
        raise V.AivError("проверка постоянства — для кадра плана (kind keyframe)")
    if client is None and not settings.gemini_api_key:
        return {"ok": True, "skipped": True, "note": "Gemini не настроен — проверка пропущена (сравни сам глазами)"}
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
        canon = _canon_refs(store, ref["project_id"], c)
        if not canon:
            continue
        cast.append(c["slug"])
        parts.append({"text": f"Персонаж id={c['slug']} «{c['name']}». Карта: {c['identity_card']}. Канон:"})
        parts.extend(inline(p) for p in canon[-2:])
    if not cast:
        return {"ok": True, "skipped": True, "note": "в плане нет персонажей с каноном — сравнивать не с чем"}
    schema = {"type": "object", "properties": {"characters": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "string"}, "score": {"type": "number"},
                                         "differences": {"type": "string"}},
        "required": ["id", "score", "differences"]}}}, "required": ["characters"]}
    from app.ai.gemini import GeminiClient, _extract_response_text

    payload = {"contents": [{"role": "user", "parts": parts}],
               "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
                                    "responseSchema": schema}}
    response = (client or GeminiClient()).generate_content(settings.gemini_text_model, payload)
    try:
        data = json.loads(_extract_response_text(response))
    except json.JSONDecodeError as exc:
        raise V.AivError("Gemini вернул не-JSON") from exc
    chars = [{"id": str(c.get("id") or ""), "score": max(0.0, min(10.0, float(c.get("score") or 0))),
              "differences": P._text(c.get("differences"), 300)} for c in data.get("characters") or []]
    score = {"characters": chars, "min": min((c["score"] for c in chars), default=None),
             "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": settings.gemini_text_model}
    V.set_ref_score(store, ref_id, score)
    low = [c for c in chars if c["score"] < 6]
    return {"ok": True, "score": score,
            "note": ("ниже 6 — перегенерируй кадр (aiv_regenerate, дёшево): " + "; ".join(
                f"{c['id']}: {c['differences']}" for c in low)) if low else "персонажи похожи на канон"}


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
