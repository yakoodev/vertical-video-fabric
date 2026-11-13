"""MCP-инструменты рендера клипа: ``render_clip`` и ``render_status`` (app/render_jobs.py).

Внешним агентам (MCP) рендер был недоступен: плейбук отсылал к ``vvf.py render`` и ``POST
/api/clip-plans/{id}/render``, до которых у них нет доступа. Теперь тот же рендер (настройки клипа, очередь тяжёлых,
QC) идёт заданием: ``render_clip`` → ``render_id``, ``render_status`` → этап, а по готовности — id рендера, ссылка
на файл и отчёт QC. Дальше ``lint_clip`` (вспышки и луп — по этому файлу) и ``submit_for_approval``.

Основа монтажа (``app/modules.CORE_TOOLS``): без рендера нечего сдавать. Цена — CPU сервиса (``cost=("cpu",)``).
"""

from __future__ import annotations

from app import render_jobs as RJ
from app.tool_core import Tool, ToolContext, ToolError, _obj

MAX_WAIT_SEC = 50  # длинный опрос render_status: меньше типичного таймаута вызова MCP


def _status_view(job: dict) -> dict:
    out = {"ok": job["status"] != "failed", "render_id": job["render_id"], "clip_id": job["clip_id"],
           "status": job["status"], "created_at": job["created_at"], "started_at": job["started_at"],
           "finished_at": job["finished_at"]}
    if job["status"] in RJ.ACTIVE:
        out["note"] = ("рендер идёт (очередь тяжёлых задач — одно за раз, обычно 1–5 мин): "
                       "render_status(render_id, wait_sec=30) ещё раз")
    elif job["status"] == "done":
        out.update(job.get("result") or {})
        out["next"] = ("готово: lint_clip (вспышки и луп — по этому файлу), contact_sheet для взгляда, потом "
                       "submit_for_approval(clip_id, title, …)")
        if out.get("voice_ok") is False:
            # Файл есть, но озвучка битая: не сдавать как готовое.
            out["note"] = f"файл готов, но {out['voice']}"
            out["next"] = ("сначала озвучка: check_voiceover(clip_id) — там fix у каждой находки; поправь, render_clip "
                           "снова. Такой ролик не выдавай за готовый")
    else:
        out["error"] = job.get("error") or "рендер не удался"
        if job.get("result"):
            out.update({k: v for k, v in job["result"].items() if k != "error"})
        out["next"] = "поправь клип по ошибке и запусти render_clip снова"
    return out


def h_render_clip(ctx: ToolContext, _args: dict) -> dict:
    job = RJ.start(ctx.store, ctx.plan_id)
    out = _status_view(job)
    out["reused"] = bool(job.get("reused"))
    out["note"] = (("рендер этого клипа уже идёт — это он. " if out["reused"] else "рендер поставлен в очередь. ")
                   + "Статус: render_status(render_id, wait_sec=30); готовый файл — в ответе статуса")
    return out


_DB_STATUS = {"succeeded": "done", "failed": "failed", "queued": "queued", "rendering": "rendering"}


def _db_render_view(ctx: ToolContext, render_clip_id: int) -> dict:
    """Готовый рендер по номеру из базы (render_clip_id из lint_clip/get_clip, а не id задания r…): повтор 13.11,
    RC3 — агент дал render_status число 1047 из lint_clip и получил «нет задания»."""
    try:
        clip = ctx.store.get_clip(int(render_clip_id))
    except KeyError:
        clip = None
    if not clip:
        raise ToolError(f"нет ни задания рендера, ни готового рендера #{render_clip_id}: id задания — из render_clip "
                        "(вида r…), номер готового рендера — render_clip_id в get_clip / lint_clip")
    status = _DB_STATUS.get(str(clip.get("status") or ""), str(clip.get("status") or ""))
    out = {"ok": status != "failed", "render_id": None, "render_clip_id": int(clip["id"]),
           "clip_id": clip.get("clip_plan_id"), "status": status, "created_at": clip.get("created_at"),
           "finished_at": clip.get("updated_at") if status in ("done", "failed") else None,
           "note": (f"#{clip['id']} — номер готового рендера в базе (render_clip_id), не задание: статус — из базы. "
                    "Новый рендер — render_clip(clip_id), его задание — render_id вида r…")}
    out.update({k: v for k, v in RJ._brief_clip(clip).items() if k not in ("render_status",)})
    if status == "done":
        out["next"] = "готово: lint_clip, contact_sheet, потом submit_for_approval(clip_id, title, …)"
    elif status == "failed":
        out["next"] = "поправь клип по ошибке и запусти render_clip снова"
    return out


def h_render_status(ctx: ToolContext, args: dict) -> dict:
    render_id = str(args.get("render_id") or "").strip().lstrip("#")
    if not render_id:
        raise ToolError("нужен render_id (из render_clip)")
    if render_id.isdigit() and RJ.get(render_id) is None:
        # Число — номер готового рендера в базе (render_clip_id), а не id задания: отвечаем из базы.
        return _db_render_view(ctx, int(render_id))
    try:
        wait = min(MAX_WAIT_SEC, max(0.0, float(args.get("wait_sec") or 0)))
    except (TypeError, ValueError):
        raise ToolError("wait_sec — число секунд (0–50)") from None
    job = RJ.wait(render_id, wait) if wait else RJ.get(render_id)
    if job is None:
        raise ToolError(f"нет задания рендера {render_id}: задания живут до перезапуска сервиса — готовые рендеры "
                        "клипа видны в get_clip; запусти render_clip снова, если файла нет")
    return _status_view(job)


def tools() -> list[Tool]:
    return [
        Tool(
            "render_clip", "Рендерю клип",
            "Рендер клипа в mp4 — тот же, что кнопка «Рендер» и POST /api/clip-plans/{id}/render: настройки клипа "
            "(формат, субтитры, слои, музыка), очередь тяжёлых задач сервиса (одно за раз), отчёт QC. Идёт в фоне: "
            "ответ сразу — render_id и status (queued|rendering); дальше render_status(render_id, wait_sec). Клип "
            "уже рендерится — вернёт то же задание. Нужен перед lint_clip (вспышки, луп) и submit_for_approval. "
            "cost: cpu (минуты CPU сервиса). / Render the clip to mp4 in the background; poll render_status.",
            _obj({}),
            h_render_clip, mutating=True, scope="clip", cost=("cpu",),
            label=lambda a: "Рендерю клип",
        ),
        Tool(
            "render_status", "Смотрю рендер",
            "Статус задания render_clip: status queued|rendering|done|failed; done — render_clip_id (id готового "
            "рендера), video_url, длительность, размер и qc (отчёт проверки файла); voice — вердикт озвучки («озвучка "
            "с ошибками: …» — файл есть, но сдавать нельзя: check_voiceover и поправить); failed — error. wait_sec (до 50) — "
            "подождать окончания в этом вызове, чтобы не опрашивать часто. render_id — id задания (r…) из render_clip; "
            "число — номер готового рендера (render_clip_id из get_clip / lint_clip): статус из базы. "
            "/ Status of a render_clip job (or of a finished render by its numeric render_clip_id).",
            _obj({"render_id": {"type": "string",
                                "description": "id задания из render_clip (r…) или номер готового рендера render_clip_id"},
                  "wait_sec": {"type": "number", "minimum": 0, "maximum": MAX_WAIT_SEC,
                               "description": "ждать окончания до N секунд (0 — ответить сразу)"}}, ["render_id"]),
            h_render_status, scope="global",
            label=lambda a: "Смотрю, как идёт рендер",
        ),
    ]
