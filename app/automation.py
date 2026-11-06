from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any

from app import agent_settings as S
from app import autopilot as A
from app import projects as P
from app import stream_questions as Q

INBOX_KEY = "auto_inbox_project_id"
INBOX_NAME = "Авто — без серии"


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _safe_error(exc: object) -> str:
    text = str(exc or "").strip()
    return text[:500] or "automation failed"


# --------------------------------------------------------------------------- #
# «Авто» → автопилот: проект и перенос настроек публикации
# --------------------------------------------------------------------------- #


def inbox_project(store: Any) -> int:
    """Проект для источников «Авто» без серии: у него очередь одобрения, слоты и аккаунты.

    Публикация идёт только через одобрение, а у одобрения и расписания всегда есть проект, поэтому
    источник без серии попадает сюда (один общий проект, а не по проекту на ссылку). Потом его
    можно перенести в нужную серию."""
    try:
        pid = int(store.get_app_setting_value(INBOX_KEY) or 0)
        if pid:
            P.get_project(store, pid, include_sources=False)
            return pid
    except (KeyError, TypeError, ValueError):
        pass
    project = P.create_project(
        store, INBOX_NAME, kind="other",
        description="Сюда «Авто» кладёт источники без серии. Клипы приходят карточками в «Одобрение»; слоты, "
                    "аккаунты и доверие — вкладка «Автопилот». Источник можно перенести в нужную серию.")
    store.set_app_setting(INBOX_KEY, int(project["id"]))
    return int(project["id"])


def slots_from_interval(hours: float, start_at: datetime | None, tz_name: str) -> list[dict]:
    """«Интервал N ч» старого «Авто» → слоты каждый день: от времени старта (или 12:00) шагом N ч."""
    tz = A.tz_of(tz_name)
    base = start_at.astimezone(tz) if start_at else datetime.now(tz).replace(hour=12, minute=0)
    step = max(1.0, float(hours))
    times, k = [], 0
    while k * step < 24 and len(times) < 24:
        moment = base + timedelta(hours=k * step)
        times.append(f"{moment.hour:02d}:{moment.minute:02d}")
        k += 1
    return [{"days": list(range(7)), "times": sorted(set(times))}]


def migrate_publish_settings(store: Any, project_id: int, publish: dict) -> list[str]:
    """Настройки публикации «Авто» (аккаунты, приватность, интервал) → автопилот проекта, без потерь:
    у ещё не настроенного проекта переносится всё, у настроенного — только пустые поля."""
    targets = [int(t) for t in publish.get("targets") or []]
    interval = float(publish.get("interval_hours") or 0)
    if not targets and interval <= 0:
        return []
    fresh = str(A._state_row(store, project_id).get("config_json") or "").strip() in ("", "{}")
    cfg = A.get_config(store, project_id)
    accounts: dict[str, list[int]] = {}
    for account_id in targets:
        try:
            account = store.get_account(account_id, include_secret=False)
        except KeyError:
            continue
        if account["platform"] in A.PLATFORMS:
            accounts.setdefault(account["platform"], []).append(int(account["id"]))
    patch: dict = {}
    if accounts and (fresh or not cfg["accounts"]):
        patch["accounts"] = accounts
    if accounts and (fresh or not cfg["platforms"]):
        patch["platforms"] = list(accounts)
    if fresh and publish.get("privacy") in ("public", "unlisted", "private"):
        patch["privacy"] = publish["privacy"]
    if interval > 0 and (fresh or not cfg["slots"]):
        patch["slots"] = slots_from_interval(interval, publish.get("start_at"), cfg["timezone"])
    if not patch:
        return []
    A.update_config(store, project_id, patch)
    notes = [f"{key}" for key in patch]
    A.log_event(store, None, project_id, "auto_migrated", "auto",
                "настройки «Авто» перенесены в автопилот: " + ", ".join(notes), {"patch": patch})
    return notes


class AutomationService:
    """Конвейер «Авто»: скачать → проанализировать → оформить → отрендерить → на одобрение.

    Публикует только одобрение (app/autopilot.py): каждый готовый клип уходит ``submit_clip``
    карточкой в очередь проекта; без карточки заданий публикации нет. Исключение — настройка
    проекта «Публиковать самому» (это решает сам ``submit_clip``). Источник без серии —
    в общий проект ``inbox_project``. Каждый запуск — свой поток; статус шагов в памяти (новые
    первыми) для страницы «Авто»; ``source_id`` — готовый источник (наблюдатель VOD,
    app/vod_watch.py), иначе загрузка по ``url`` фоновой ``DownloadService``.
    """

    def __init__(self, store, ingestor, analysis_service, render_service, download_service=None) -> None:
        self.store = store
        self.ingestor = ingestor
        self.analysis = analysis_service
        self.render = render_service
        self.downloads = download_service
        self._runs: list[dict] = []
        self._lock = threading.Lock()
        self._ids = count(1)

    def list_runs(self) -> list[dict]:
        with self._lock:
            return [dict(run) for run in self._runs[:20]]

    def get_run(self, run_id: int) -> dict | None:
        with self._lock:
            return next((dict(r) for r in self._runs if r["id"] == run_id), None)

    def _new_run(self, **fields: Any) -> dict:
        with self._lock:
            run = {
                "id": next(self._ids),
                "status": "queued",
                "message": "",
                "error": "",
                "source_id": None,
                "project_id": None,
                "plans": 0,
                "clips": 0,
                "jobs": 0,  # прежнее поле: теперь всегда 0 — заданий «Авто» само не создаёт
                "approvals": 0,
                "auto_approved": 0,
                "warnings": [],
                "created_at": _now_text(),
            }
            run.update(fields)
            self._runs.insert(0, run)
            del self._runs[50:]
            return run

    def _update(self, run: dict, **fields: Any) -> None:
        with self._lock:
            run.update(fields)

    def start_run(
        self,
        *,
        url: str = "",
        smotvibe_selection: dict | None = None,
        analysis: dict,
        render: dict,
        publish: dict,
        source_id: int | None = None,
        project_id: int | None = None,
        look: dict | None = None,
        label: str = "",
        owner_started: bool = False,
    ) -> dict:
        """``owner_started`` — владелец сам нажал запуск в браузере: его выбор идти до рендера. Наблюдатель VOD и запуск
        по токену этого выбора не сделали и останавливаются по настройкам проекта (app/agent_settings.py)."""
        run = self._new_run(label=(label or publish.get("label") or url or "Автонарезка"), url=url,
                            source_id=source_id, project_id=project_id)
        thread = threading.Thread(
            target=self._run,
            args=(run, url, smotvibe_selection or {}, analysis, render, publish),
            kwargs={"source_id": source_id, "project_id": project_id, "look": look, "owner_started": owner_started},
            name=f"automation-{run['id']}",
            daemon=True,
        )
        thread.start()
        return dict(run)

    def _run(self, run, url, selection, analysis, render, publish, source_id=None, project_id=None, look=None,
             owner_started=False) -> None:
        try:
            if source_id:
                source = self.store.get_source(int(source_id))
            else:
                self._update(run, status="downloading", message="Скачиваю источник…")
                source = self._ingest(url, selection)
            self._update(run, source_id=source["id"])
            if source.get("status") == "failed":
                raise RuntimeError(source.get("error") or "не удалось скачать источник")
            project_id = self._project_for(source, project_id)
            self._update(run, project_id=project_id)
            migrated = migrate_publish_settings(self.store, project_id, publish)
            if migrated:
                self._warn(run, "аккаунты/слоты «Авто» перенесены в автопилот проекта")
            if not owner_started:
                refusal = Q.montage_refusal(self.store, int(source["id"]))
                if refusal:
                    raise RuntimeError(refusal["error"])

            self._update(run, status="analyzing", message="Анализирую видео…")
            self.analysis.run_analysis(
                source["id"],
                provider=analysis.get("provider") or None,
                model=analysis.get("model") or None,
                prompt=analysis.get("prompt") or None,
                use_transcript=analysis.get("use_transcript"),
                **({"output_format": analysis["format"]} if analysis.get("format") else {}),
            )
            self.store.ensure_clip_plans_for_source(source["id"])
            from app.chat_signal import rerank

            # Порядок анализа, мягко поправленный всплесками чата Twitch (если чат есть).
            plans = rerank(self.store, self.store.list_clip_plans(source_id=source["id"]))
            max_clips = int(publish.get("max_clips") or 0)
            if max_clips > 0:
                plans = plans[:max_clips]
            self._update(run, plans=len(plans))
            if not plans:
                raise RuntimeError("анализ не дал клипов")
            waiting = [p for p in plans if S.awaits_pick(self.store, int(p["id"]))]
            if owner_started:
                S.set_picked_many(self.store, [p["id"] for p in plans])
            elif waiting and S.get_settings(self.store, project_id)["stop_at_clip_list"]:
                message = S.list_stop_message(project_id, len(waiting))
                A.log_event(self.store, None, project_id, "clip_list_stop", "auto", message)
                self._update(run, status="done", message=message, stopped_at_list=True)
                return

            from app import modules

            if publish.get("ai_montage") and not modules.is_enabled("ai_montage", self.store):
                self._warn(run, "ИИ-монтаж пропущен: модуль выключен в настройках")
            elif publish.get("ai_montage"):
                self._update(run, message=f"🤖 ИИ-монтаж клипов ({len(plans)})…")
                from app.ai.montage import montage_many

                montage_many(self.store, [p["id"] for p in plans])  # best effort per clip

            if look:
                self._update(run, message="Оформление проекта…")
                brand_id = P.get_project(self.store, project_id, include_sources=False).get("brand_id")
                for plan in plans:
                    for problem in self._apply_look(int(plan["id"]), look, brand_id):
                        self._warn(run, f"клип #{plan['id']}: {problem}")

            self._update(run, status="rendering", message=f"Рендерю клипы (0/{len(plans)})…")
            done: list[tuple[int, dict]] = []
            for index, plan in enumerate(plans, start=1):
                clip = self.render.render_clip_plan(plan["id"], **render)
                if clip.get("status") == "succeeded":
                    done.append((int(plan["id"]), clip))
                self._update(run, clips=len(done), message=f"Рендерю клипы ({index}/{len(plans)})…")
            if not done:
                raise RuntimeError("ни один клип не отрендерился")

            if publish.get("ai_metadata"):
                self._update(run, message="Пишу заголовки и описания…")
                from app.ai.publish_meta import apply_ai_metadata

                # Уже написанное после рендера (auto_publish_meta) второй раз не генерируем.
                apply_ai_metadata(self.store, [c["id"] for _, c in done], only_missing=True)
                done = [(pid, self.store.get_clip(c["id"])) for pid, c in done]

            summary = f"Готово: клипов {len(done)}"
            if self._wants_cards(project_id, publish):
                self._update(run, status="scheduling", message="Отправляю на одобрение…")
                pending = auto = 0
                platforms = self._platforms_of(publish.get("targets") or [])
                for plan_id, clip in done:
                    try:
                        card = A.submit_clip(self.store, plan_id, title=clip.get("title") or "",
                                             description=clip.get("description") or "", platforms=platforms or None,
                                             submitted_by="auto")
                    except (KeyError, ValueError) as exc:
                        self._warn(run, f"клип #{plan_id} не отправлен: {exc}")
                        continue
                    if card["status"] == "approved":
                        auto += 1
                    else:
                        pending += 1
                self._update(run, approvals=pending, auto_approved=auto)
                summary += f", на одобрении {pending}" + (f", сразу в расписание {auto} (настройка «Публиковать самому»)" if auto else "")
            else:
                summary += ", без публикации"
            self._update(run, status="done", message=summary)
        except Exception as exc:  # noqa: BLE001 - automation failures are surfaced on the run
            self._update(run, status="failed", error=_safe_error(exc), message="Ошибка")

    def _warn(self, run: dict, text: str) -> None:
        with self._lock:
            run["warnings"] = (run.get("warnings") or []) + [text[:300]]

    def _project_for(self, source: dict, project_id: int | None) -> int:
        if project_id:
            if P.project_of_source(self.store, int(source["id"])) != int(project_id):
                P.set_source_project(self.store, int(source["id"]), int(project_id))
            return int(project_id)
        current = P.project_of_source(self.store, int(source["id"]))
        if current:
            return current
        inbox = inbox_project(self.store)
        P.set_source_project(self.store, int(source["id"]), inbox)
        return inbox

    def _platforms_of(self, targets: list) -> list[str]:
        out = []
        for account_id in targets:
            try:
                platform = self.store.get_account(int(account_id), include_secret=False)["platform"]
            except (KeyError, TypeError, ValueError):
                continue
            if platform in A.PLATFORMS and platform not in out:
                out.append(platform)
        return out

    def _wants_cards(self, project_id: int, publish: dict) -> bool:
        """Карточки — если «Авто» выбрало аккаунты, просили явно или у проекта есть площадки (как раньше:
        без аккаунтов и площадок — «без публикации»)."""
        return bool(publish.get("submit") or publish.get("targets") or A.get_config(self.store, project_id)["platforms"])

    def _apply_look(self, plan_id: int, look: dict, brand_id: int | None) -> list[str]:
        """Оформление проекта инструментами монтажёра: макет, мат, плашка и концовка бренда."""
        from app import agent_tools as T
        from app.clip_history import snapshot

        steps: list[tuple[str, dict]] = []
        if look.get("layout"):
            steps.append(("set_layout", {"mode": look["layout"]}))
        if look.get("censor_profanity"):
            steps.append(("censor_profanity", {"verify": False}))
        problems = []
        if (look.get("nameplate") or look.get("end_screen")) and not brand_id:
            problems.append("у проекта нет бренда — плашка канала и концовка не ставятся")
        elif brand_id:
            if look.get("nameplate"):
                steps.append(("set_nameplate", {"brand_id": int(brand_id)}))
            if look.get("end_screen"):
                steps.append(("set_end_screen", {"brand_id": int(brand_id)}))
        ctx = T.ToolContext(store=self.store, clip_plan_id=plan_id,
                            on_first_change=lambda: snapshot(self.store, plan_id, "автопилот: оформление проекта"))
        for name, args in steps:
            result, ok = T.run_tool(ctx, name, args)
            if not ok:
                problems.append(f"{name}: {(result or {}).get('error') if isinstance(result, dict) else result}")
        return problems

    def _ingest(self, url: str, selection: dict) -> dict:
        if self.downloads is not None:  # фоновая загрузка: видна в «Активности», отменяется
            task = self.downloads.start(url, selection=selection or None)
            while task["status"] in ("queued", "downloading", "cancelling"):
                time.sleep(1.0)
                task = self.downloads.get(task["id"])
            if task.get("source_id"):
                return self.store.get_source(int(task["source_id"]))
            raise RuntimeError(task.get("error") or ("загрузка отменена" if task["status"] == "cancelled"
                                                     else "не удалось скачать источник"))
        if selection and selection.get("media_url"):
            return self.ingestor.ingest_smotvibe_selection(
                url,
                media_url=selection["media_url"],
                referer=selection.get("referer", ""),
                audio_format_id=selection.get("audio_format_id", ""),
                filename_label=selection.get("filename_label", ""),
            )
        return self.ingestor.ingest_url(url)
