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

Настройка проекта «Тратить без подтверждения» (app/agent_settings.py): этап в пределах суммы за раз и за месяц
одобряет не кнопка, а настройка. Карточка та же, потолки те же, ``decided_by`` — ``setting:spend``, в журнале трат
отметка. Перед каждой оплатой запуска настройка проверяется заново: выключили — запуск ``held``, нужна новая карточка.

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

Где что лежит. Здесь — исполнение запусков и всё, что охраняет оплату: ``_preflight``, ``_claim``, ``_send``, ``_mark_uncertain``,
``_recover``, ``run_jobs``, ``resume``, работник. Вынесено: деньги ``app/aivideo_budget.py``, запись запуска ``app/aivideo_jobs.py``,
промпты ``app/aivideo_prompts.py``, проверки ``app/aivideo_checks.py``, план и зависимости ``app/aivideo_plan.py``, оценка и карточки
``app/aivideo_cards.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда. Функции, читающие
имена, которые тесты подменяют здесь (``RETRY_BACKOFF_SEC``, ``BALANCE_SETTLE_SEC``, ``RECOVER_INTERVAL_SEC``, ``_vision_client``),
должны оставаться в этом файле.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import agent_settings as S
from app import aivideo as V
from app import autopilot as A
from app import projects as P
from app.aivideo_providers import (
    ProviderError, SubmitUncertain, choose_video_mode, video_capabilities, model_params, video_params, network_phase
)
from app.settings import settings

from app.aivideo_budget import (  # noqa: F401
    STAGE_LABELS, EPISODE_CAP_RUB, ACTIVE, OPEN, REF_LABELS, BudgetError, EXTENSIONS, register_extension,
    _ext_of_target, _ext_of_kind, _sum, spent_month, spent_script, spent_approval, spent_parts, spent_on_target,
    _reserved, _month_filter, episode_cap, project_month_cap, budget_view, cap_problems, add_ledger, ledger,
)
from app.aivideo_jobs import (  # noqa: F401
    _log, _fresh, _is_chain, _fresh_video, _usable, get_job, _set_job, _cas, _runner_id, _save_asset, _utc_now,
    _cached, _variant, _neighbors, _run_blocking, _result_ref, list_jobs, job_counts, cancel_jobs,
)
from app.aivideo_prompts import (  # noqa: F401
    STYLE_REF_COUNT, VIDEO_MIN_SEC, VIDEO_MAX_SEC, STYLE_BASE, NO_TEXT, REF_ASPECT, FRAME_KINDS, VIDEO_NEGATIVE,
    _quality, _parse_item, _image_item, SFX_DEFAULT, SFX_BY_EMOTION, sfx_for, speech_of, _voice_of, _line_key,
    _paid, speech_need_sec, video_seconds, _video_item, TAIL_SOURCE, series_video_model, tail_mode, _tail_marker,
    tail_start_ref, make_tail_start, start_frame_ref, end_frame_ref, _share_item, video_model_for, _video_busy,
    BUSY, _style_text, _asset_path, _canon_refs, video_last_frame, _prev_frame, _style_paths, _identity, PHYSICS,
    LOCATION_PLATE_HINT, LOCATION_VIDEO_HINT, shot_location, location_text, location_plate, continuity_shot,
    _frame_request, PLATE_AVOID, plate_prompt, build_request, _file_digest, input_hash, build_video_prompt,
    video_negative, GENTLE_CAMERA, CAMERA_TEXT, _video_takes_refs, _cast_sheets,
)
from app.aivideo_checks import (  # noqa: F401
    _vision_client, VIDEO_QA_FRAMES, sample_video_frames, video_qa_flags, CONTINUITY_PROMPT, _inline,
    _save_continuity, check_continuity, continuity_flags,
)
from app.aivideo_plan import (  # noqa: F401
    STAGES, plan_stage, _plan_video, _frame_deps, _video_deps, _deps_busy, _after_video, _after_frame,
)
from app.aivideo_cards import (  # noqa: F401
    CARD_ACTION, estimate, _target_key, _busy_jobs, _replaceable_cards, _card_keys, _insert_job, _card_question,
    _request_locked, _project_of_target, _stage_of, stage_approvals, decide_card,
)

SUBMIT_STALE_SEC = 600  # «отправляю» дольше — исполнитель другого процесса умер (POST ждёт ≤ 120 с)


# Поиск запроса у провайдера после обрыва ответа: сразу RECOVER_ATTEMPTS раз через
# RECOVER_INTERVAL_SEC, дальше — каждым проходом работника; не нашли за срок адаптера
# (``recover_deadline_sec``, по умолчанию RECOVER_DEADLINE_SEC) — «под вопросом» (uncertain).
RECOVER_ATTEMPTS = 6
RECOVER_INTERVAL_SEC = 10.0
RECOVER_DEADLINE_SEC = 20 * 60


# Сеть до провайдера рвётся (TLS EOF, таймауты). До отправки тела — повтор сам, с паузой;
# после — «под вопросом» (uncertain): сверка баланса, сам не повторяю.
SUBMIT_TRIES = 3
RETRY_BACKOFF_SEC = (2.0, 6.0)
BALANCE_SETTLE_SEC = 3.0  # провайдер списывает не мгновенно — баланс «после» чуть позже
STOP_HTTP = (401, 402, 403)  # ключ/деньги/доступ — дальше всё равно откажет: стоп партии


_LOCK = threading.RLock()
_ACTIVE_JOBS: set[int] = set()  # опрашиваются сейчас (не опрашивать вторым потоком)
_INFLIGHT: set[int] = set()  # взяты в работу этим процессом (resume их не трогает)
_wake: threading.Event | None = None


_REQUEST_LOCK = threading.Lock()  # два одновременных запроса этапа (двойной клик) — по очереди


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
        est = res["estimate"]
        dry = all(i["provider"] == "none" for i in est["items"])
        res = {"ok": True, "status": "done", "estimate": est, "run": run_jobs(store, res["job_ids"]),
               "note": ("сухой прогон: заглушки вместо картинок, тон вместо голоса, 0 ₽" if dry
                        else "бесплатно (Gemini TTS, настоящий голос), 0 ₽")}
    return res


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
    if card.get("decided_by") == S.SPEND_ACTOR:
        out += S.spend_problems_at_run(store, int(job["project_id"]), amount, job)
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
        dialog = params.get("dialog") or []
        if dialog and hasattr(tts, "synthesize_dialog"):
            # реплики героя — своим голосом (3D-серия: рот героя двигается только на них)
            voices = {"narrator": voice, "hero": params.get("hero_voice") or ""}
            speech = _send(store, job, lambda: tts.synthesize_dialog(dialog, raw, voices))
        else:
            speech = _send(store, job, lambda: tts.synthesize(text, raw))
        cost = speech.cost_rub
        source = "provider" if cost is not None else "estimate"
        cost = float(cost) if cost is not None else float(job["estimate_rub"])
        asset = _save_asset(store, job, Path(speech.path), "audio", "audio/mpeg", f"ИИ-видео: {job['label']}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    key = _line_key({"provider": job["provider"], "model": job["model"], "voice": voice, "style": style}, text)
    return _done(store, job, asset["id"], key, cost, source)


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


_SHEETS_HINT = ("The characters must look exactly like in the reference character sheets (same design, shape, "
                "proportions and colours); never redesign them.")
_PLATE_HINT = ("The first reference image is the background plate of the location: keep exactly this room (layout, "
               "furniture, lighting, colours).")


WAIT = object()  # запуск ждёт, пока делается то, от чего он зависит (кадр-основа, разворот)


def _run_chain(store: Any, job: dict) -> Any:
    """Сцепка «последний кадр видео → старт» (v2) упразднена: старт сцепленного плана — общий
    конечный кадр прошлого плана (этап keyframes). Старые запуски из очереди — отмена без денег."""
    _cas(store, job["id"], ("submitting",), status="cancelled", cost_rub=0.0, cost_source="free",
         error="устарело: старт сцепленного плана — конечный кадр прошлого плана (этап keyframes), из видео "
               "ничего не вынимается")
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


# --------------------------------------------------------------------------- #
# Проверка постоянства (Gemini-зрение, бесплатно; нет ключа — пропуск)
# --------------------------------------------------------------------------- #

REVIEW_MIN_SCORE = 6.0  # ниже — версия «проверь» (needs_review); перерисовка — только по решению владельца
AUTO_CHECK = True  # проверять кадр с персонажами сразу после генерации (Gemini, бесплатно)


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
